"""Executable documentation keeps its real test obligations, independently of its folder."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from anaxigraph import recorder
from anaxigraph.application import Application
from anaxigraph.indexing.test_record import junit_cases

FILES = {
    "docs/__init__.py": "",
    "docs/rules.py": "def positive(value):\n    return value > 0\n",
    "docs/limits.py": "LIMIT = 3\n",
    "docs/guide.md": "A guide for readers.\n",
    "docs/settings.json": '{"limit": 3}\n',
    "app/__init__.py": "",
    "app/limits.py": "from docs.limits import LIMIT\ndef allowed(value):\n    return value <= LIMIT\n",
    "app/data.py": (
        "import json\nfrom pathlib import Path\ndef limit():\n"
        "    path = Path(__file__).parents[1] / 'docs/settings.json'\n"
        "    return json.loads(path.read_text())['limit']\n"
    ),
    "app/independent.py": "def value():\n    return 1\n",
    "tests/test_rules.py": (
        "from docs.rules import positive\ndef test_positive():\n    assert positive(2)\n"
    ),
    "tests/test_limits.py": (
        "from app.limits import allowed\ndef test_allowed():\n    assert allowed(2)\n"
    ),
    "tests/test_limits_other.py": (
        "from app.limits import allowed\ndef test_upper_limit():\n    assert allowed(3)\n"
    ),
    "tests/test_data.py": (
        "from app.data import limit\ndef test_data():\n    assert limit() == 3\n"
    ),
    "tests/test_independent.py": (
        "from app.independent import value\ndef test_independent():\n    assert value() == 1\n"
    ),
}


def _pytest(root: Path, report: Path, recording: dict[str, str] | None = None):
    environment = os.environ | {"PYTHONDONTWRITEBYTECODE": "1"}
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--junitxml={report}",
    ]
    if recording:
        environment |= recording
        command += [
            "-p",
            "anaxigraph_reads",
            "--cov=docs",
            "--cov=app",
            "--cov-context=test",
            "--cov-report=",
        ]
    return subprocess.run(command, cwd=root, env=environment, text=True, capture_output=True)


@pytest.fixture(scope="module")
def recorded_documentation(tmp_path_factory):
    folder = tmp_path_factory.mktemp("documentation-consumers")
    root = folder / "source"
    for name, content in FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    app = Application(root)
    manifest = app.start_recording("documentation")["manifest"]
    report = folder / "baseline.xml"
    done = _pytest(
        root,
        report,
        {
            "PYTHONPATH": str(Path(recorder.__file__).parent),
            "ANAXIGRAPH_ROOT": str(root),
            "ANAXIGRAPH_RECORDING": manifest,
            "ANAXIGRAPH_READS": str(folder / "reads.json"),
            "COVERAGE_FILE": str(folder / ".coverage"),
        },
    )
    assert done.returncode == 0, done.stdout + done.stderr
    saved = app.record_tests(
        folder / ".coverage",
        suite="documentation",
        scope=["."],
        junit=[report],
        reads=folder / "reads.json",
    )
    assert saved["provenance"]["status"] == "verified"
    assert saved["provenance"]["observations"] == "anaxigraph-reads-v3"
    return root


@pytest.fixture
def documentation_checkout(recorded_documentation, tmp_path):
    return Path(shutil.copytree(recorded_documentation, tmp_path / "checkout"))


@pytest.mark.parametrize(
    ("path", "content", "failing"),
    [
        ("docs/rules.py", "def positive(value):\n    return value < 0\n", {"tests/test_rules.py"}),
        (
            "docs/limits.py",
            "LIMIT = 1\n",
            {"tests/test_limits.py", "tests/test_limits_other.py"},
        ),
        ("docs/settings.json", '{"limit": 1}\n', {"tests/test_data.py"}),
    ],
)
def test_documentation_faults_keep_every_observed_failing_consumer(
    documentation_checkout, tmp_path, path, content, failing
):
    root = documentation_checkout
    (root / path).write_text(content)
    app = Application(root)
    [suite] = app.tests_for_change()["suites"]
    report = tmp_path / "mutated.xml"
    done = _pytest(root, report)
    assert done.returncode == 1, done.stdout + done.stderr
    _, failures = junit_cases([report], ".", app._current().snapshot.files)
    assert set(failures) == failing
    assert suite["status"] == "selected"
    assert set(suite["test_files"]) == failing
    assert "tests/test_independent.py" not in suite["test_files"]


def test_new_unattributed_documentation_source_retains_full_check_fallback(documentation_checkout):
    root = documentation_checkout
    (root / "docs/new_hook.py").write_text("def hook():\n    return 1\n")
    [suite] = Application(root).tests_for_change()["suites"]
    assert suite["status"] == "all"
    assert suite["selected"] == suite["total"] == 5
    assert any("docs/new_hook.py" in reason for reason in suite["everything_because"])


def test_unread_documentation_prose_stays_out_of_source_test_selection(documentation_checkout):
    root = documentation_checkout
    (root / "docs/guide.md").write_text("An updated guide for readers.\n")
    [suite] = Application(root).tests_for_change()["suites"]
    assert suite["status"] == "nothing_to_run"
    assert suite["test_files"] == []


def test_documentation_fixture_records_code_separately_from_prose(recorded_documentation):
    record = json.loads(
        (recorded_documentation / ".anaxigraph-v2/test-records/documentation.json").read_text()
    )
    assert record["executed"]["docs/rules.py"]
    assert "docs/limits.py" in record["imports"]
    assert "docs/guide.md" not in record["executed"]
