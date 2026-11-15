"""A checkout shaped like a real product: several projects one folder below the root.

Two sibling projects both contain a package called ``app``; tests sit in folders without
``__init__.py``; names are re-exported through a package; module-level values are imported.
Small single-package fixtures at the repository root cannot show any of these.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anaxigraph.analysis.health import map_health
from anaxigraph.application import Application
from anaxigraph.contracts import Edge, Graph, Node, Snapshot
from anaxigraph.indexing import index

LAYOUT = {
    "backend/app/__init__.py": "",
    "backend/app/config.py": (
        "class Settings:\n    debug = False\n\n\nsettings = Settings()\n\n\n"
        "def get_settings():\n    return settings\n"
    ),
    "backend/app/models/__init__.py": (
        "from .account import Account\nfrom .invoice import Invoice as Bill\n\n"
        '__all__ = ["Account", "Bill"]\n'
    ),
    "backend/app/models/account.py": "class Account:\n    pass\n",
    "backend/app/models/invoice.py": "class Invoice:\n    pass\n",
    "backend/app/services/__init__.py": "",
    "backend/app/services/audit.py": (
        "EVENTS = []\n\n\ndef record(event):\n    EVENTS.append(event)\n"
    ),
    "backend/app/services/billing.py": (
        "from app.config import settings\n"
        "from app.models import Account, Bill\n"
        "from app.services import audit\n"
        "from app.services.audit import EVENTS\n"
        "from ..config import get_settings\n\n\n"
        "class Premium(Account):\n    pass\n\n\n"
        "def charge(account: Account) -> Bill:\n"
        "    audit.record(settings.debug)\n"
        "    get_settings()\n"
        "    return Bill()\n"
    ),
    "backend/tests/conftest.py": "import pytest\n",
    "backend/tests/billing_support.py": "def account():\n    return object()\n",
    "backend/tests/test_billing.py": (
        "from app.services.billing import charge\n"
        "from billing_support import account\n"
        "from tests.billing_support import account as same\n\n\n"
        "def test_charge():\n    assert charge(account())\n"
    ),
    "runner/app/__init__.py": "",
    "runner/app/config.py": 'settings = {"mode": "runner"}\n',
    "runner/app/main.py": "from app.config import settings\n\n\ndef run():\n    return settings\n",
    "runner/tests/test_main.py": (
        "from app.main import run\n\n\ndef test_run():\n    assert run()\n"
    ),
    "worker/jobs/__init__.py": "",
    "worker/jobs/nightly.py": (
        "import json\nimport logging\n\n\ndef job():\n    return json.dumps({})\n"
    ),
    "scripts/logging.py": "def setup():\n    pass\n",
    "scripts/tool.py": "import logging\nfrom app.config import settings\n",
    "src/library/__init__.py": "",
    "src/library/core.py": "def core():\n    return 1\n",
    "tests/test_library.py": (
        "from library.core import core\n\n\ndef test_core():\n    assert core()\n"
    ),
}


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    root = tmp_path_factory.mktemp("product")
    for relative, content in LAYOUT.items():
        path = Path(root, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return index(root)


def imported(graph, path: str) -> set[str]:
    """Targets of the import statements in one file that were linked inside the checkout."""
    return {
        edge.target
        for edge in graph.edges
        if edge.kind == "imports" and edge.path == path and not edge.target.startswith("external:")
    }


def test_each_project_resolves_imports_from_its_own_root(graph):
    assert "module:runner/app/config.py" in imported(graph, "runner/app/main.py")
    assert "module:backend/app/config.py" not in imported(graph, "runner/app/main.py")
    billing = imported(graph, "backend/app/services/billing.py")
    assert "module:backend/app/config.py" in billing
    assert "module:runner/app/config.py" not in billing
    assert "module:backend/app/services/audit.py" in billing
    assert "symbol:backend/app/config.py#get_settings" in billing


def test_module_names_start_at_the_import_root(graph):
    names = {node.path: node.name for node in graph.nodes if node.kind == "module"}
    assert names["backend/app/services/billing.py"] == "app.services.billing"
    assert names["runner/app/main.py"] == "app.main"
    assert names["backend/tests/test_billing.py"] == "test_billing"
    assert names["src/library/core.py"] == "library.core"


def test_a_re_exported_name_links_to_its_definition_and_to_the_package(graph):
    billing = imported(graph, "backend/app/services/billing.py")
    assert "symbol:backend/app/models/account.py#Account" in billing
    assert "symbol:backend/app/models/invoice.py#Invoice" in billing
    assert "module:backend/app/models/__init__.py" in billing
    assert any(
        edge.kind == "inherits"
        and edge.source == "symbol:backend/app/services/billing.py#Premium"
        and edge.target == "symbol:backend/app/models/account.py#Account"
        and edge.resolution == "resolved"
        for edge in graph.edges
    )


def test_tests_without_package_markers_link_to_code_and_to_their_helpers(graph):
    test = imported(graph, "backend/tests/test_billing.py")
    assert "symbol:backend/app/services/billing.py#charge" in test
    assert "symbol:backend/tests/billing_support.py#account" in test
    assert "symbol:runner/app/main.py#run" in imported(graph, "runner/tests/test_main.py")
    assert "symbol:src/library/core.py#core" in imported(graph, "tests/test_library.py")
    assert any(
        edge.kind == "tests" and edge.target == "symbol:backend/app/services/billing.py#charge"
        for edge in graph.edges
    )


def test_a_standard_library_name_is_local_only_beside_the_importing_file(graph):
    assert imported(graph, "worker/jobs/nightly.py") == set()
    assert "module:scripts/logging.py" in imported(graph, "scripts/tool.py")


def test_health_reports_what_is_linked_and_what_is_not(graph):
    health = map_health(graph)
    imports = health["internal_imports"]
    # Only scripts/tool.py cannot say which project's `app` it means.
    assert imports["unlinked"] == 1
    assert imports["unlinked_examples"][0]["path"] == "scripts/tool.py"
    assert imports["linked"] >= 14
    assert imports["linked_share"] > 0.9
    assert health["test_modules"] == {"linked_to_code": 3, "total": 3}
    assert health["modules"] == {"parsed": len(LAYOUT), "parse_errors": 0}
    assert health["warning"] is None


def test_a_map_that_links_almost_nothing_says_so():
    module = Node("module:a.py", "module", "a", "a.py", 1, {"parse_status": "parsed"})
    missing = [
        Node(
            f"external:a.py#<module>:app.part{i}",
            "external",
            f"app.part{i}",
            "a.py",
            1,
            {"internal": True},
        )
        for i in range(25)
    ]
    edges = tuple(
        Edge(module.id, node.id, "imports", "a.py", line, "unresolved")
        for line, node in enumerate(missing, 1)
    )
    snapshot = Snapshot("s", "/checkout", None, False, {"a.py": "f", "ui/view.tsx": "g"}, "now")
    health = map_health(Graph(snapshot, (module, *missing), edges))
    assert health["internal_imports"]["linked_share"] == 0.0
    assert health["warning"].startswith("Only 0 of 25 imports between this checkout's own files")
    assert health["unread_source_files"] == 1


def test_scan_and_status_carry_the_health_report(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "a.py").write_text("def run():\n    return 1\n")
    (root / "test_a.py").write_text("from a import run\n\n\ndef test_run():\n    assert run()\n")
    application = Application(root, tmp_path / "knowledge.sqlite")
    health = application.scan()["map_health"]
    assert health["internal_imports"] == {
        "linked": 1,
        "unlinked": 0,
        "linked_share": 1.0,
        "unlinked_examples": [],
    }
    assert health["test_modules"] == {"linked_to_code": 1, "total": 1}
    assert application.status()["map_health"] is health
