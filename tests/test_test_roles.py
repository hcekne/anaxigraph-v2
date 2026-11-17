"""Source-supported test roles preserve lookup, graph links and explicit review scopes."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import pytest
from test_workflows import answer

import anaxigraph.indexing as indexing
from anaxigraph import recorder
from anaxigraph.analysis import plan_scopes
from anaxigraph.analysis.charter import module_facts
from anaxigraph.analysis.health import map_health
from anaxigraph.application import Application
from anaxigraph.indexing import discovery
from anaxigraph.workflows.engine import WorkflowRunner


def write(root, path, source):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)


def modules(app):
    return {node.path: node for node in app._current().nodes if node.kind == "module"}


def project(root, import_line="from conftest import account"):
    write(root, "billing.py", "def charge(value):\n    return value\n")
    write(root, "checks/conftest.py", "def account():\n    return 1\n")
    write(
        root,
        "checks/case.py",
        import_line + "\nfrom billing import charge\n\n"
        "def helper():\n    return charge(1)\n\n"
        "def test_charge_once():\n    assert helper() == 1\n",
    )
    app = Application(root)
    app.scan()
    return app


@pytest.mark.parametrize(
    "import_line",
    [
        "from conftest import account",
        "from .conftest import account",
        "from fixtures.conftest import account as example",
        "import conftest as fixtures",
    ],
)
def test_unconventional_filename_needs_both_fixture_import_and_test_definition(
    tmp_path, import_line
):
    app = project(tmp_path, import_line)
    observed = modules(app)
    test = observed["checks/case.py"]
    assert test.data["is_test"] is True
    assert test.data["test_basis"] == "conftest-import-and-test-definition"
    assert observed["billing.py"].data["is_test"] is False
    assert map_health(app._current())["test_modules"] == {"total": 1, "linked_to_code": 1}
    assert all(
        node.data["is_test"] is True
        for node in app._current().nodes
        if node.path == test.path and node.kind == "function"
    )


@pytest.mark.parametrize(
    "source",
    [
        "def test_connection():\n    return True\n",
        "from conftest import account\ndef connect():\n    return account()\n",
        "from contest import account\ndef test_charge():\n    assert account()\n",
        'description = "from conftest import account; def test_charge(): pass"\n',
        "from conftest import account\ndef factory():\n    def test_inner():\n        pass\n",
        "from conftest import account\nclass test_contract:\n    pass\n",
    ],
)
def test_one_suggestive_word_or_nested_factory_does_not_establish_a_test_role(tmp_path, source):
    write(tmp_path, "component.py", source)
    app = Application(tmp_path)
    app.scan()
    assert modules(app)["component.py"].data["is_test"] is False


def test_new_roles_create_test_links_that_survive_saved_parts_and_dependency_relinking(tmp_path):
    app = project(tmp_path)
    graph = app._current()
    billing = modules(app)["billing.py"]
    facts = module_facts(graph, billing)
    assert facts["observed_test_paths"] == ["checks/case.py"]
    test_links = [
        edge for edge in graph.edges if edge.path == "checks/case.py" and edge.kind == "tests"
    ]
    assert any(edge.target == "symbol:billing.py#charge" for edge in test_links)
    assert any(edge.target == "symbol:checks/case.py#helper" for edge in test_links)
    reopened = Application(tmp_path)
    write(tmp_path, "billing.py", "def charge(value, currency='GBP'):\n    return value\n")
    reopened.scan()
    after = reopened._current()
    links = [edge for edge in after.edges if edge.path == "checks/case.py" and edge.kind == "tests"]
    assert set(links) == set(test_links)
    assert len(links) == len(set(links))
    assert reopened.indexer.last_scan["modules_parsed"] == 1
    assert (
        modules(reopened)["checks/case.py"].data["test_basis"]
        == "conftest-import-and-test-definition"
    )
    assert module_facts(after, modules(reopened)["billing.py"])["observed_test_paths"] == [
        "checks/case.py"
    ]


def test_removing_source_evidence_clears_the_role_and_derived_test_links(tmp_path):
    app = project(tmp_path)
    source = tmp_path / "checks/case.py"
    source.write_text(source.read_text().replace("from conftest import account", "value = 1"))
    app.scan()
    node = modules(app)["checks/case.py"]
    assert node.data["is_test"] is False and "test_basis" not in node.data
    assert not any(edge.kind == "tests" and edge.path == node.path for edge in app._current().edges)
    assert any(
        edge.kind == "calls" and edge.target == "symbol:billing.py#charge"
        for edge in app._current().edges
        if edge.path == node.path
    )


def test_default_mapping_and_patterns_skip_source_supported_tests_but_explicit_scope_runs(tmp_path):
    app = project(tmp_path)
    assert plan_scopes(app._current(), "patterns") == [["module:billing.py"]]
    calls = []

    def provider(request, *_args, **_kwargs):
        calls.extend(node["path"] for node in request["packet"]["modules"])
        return answer(request)

    runner = WorkflowRunner(app, provider)
    assert runner.start("mapping", background=False)["status"] == "completed"
    assert calls == ["billing.py"]
    assert (
        runner.start("mapping", scope=["checks/case.py"], background=False)["status"] == "completed"
    )
    assert calls == ["billing.py", "checks/case.py"]


@pytest.mark.parametrize("task", ["Fix test_charge_once", "Fix checks/case.py"])
def test_explicit_test_function_and_path_still_own_a_test_change(tmp_path, task):
    app = project(tmp_path)
    brief = app.prepare_change(task)
    assert brief["likely_owners"][0]["path"] == "checks/case.py"
    if "test_charge_once" in task:
        assert brief["likely_owners"][0]["evidence"] == [{"path": "checks/case.py", "line": 7}]
    ordinary = app.prepare_change("Improve charge calculations")
    assert ordinary["likely_owners"][0]["path"] == "billing.py"


def test_explicit_test_symbol_in_a_conventional_test_folder_is_not_lost(tmp_path):
    write(tmp_path, "billing.py", "def charge():\n    return 1\n")
    write(tmp_path, "tests/test_billing.py", "def test_charge_once():\n    assert True\n")
    app = Application(tmp_path)
    brief = app.prepare_change("Fix test_charge_once")
    assert brief["likely_owners"][0]["path"] == "tests/test_billing.py"
    assert brief["likely_owners"][0]["evidence"] == [{"path": "tests/test_billing.py", "line": 1}]


def test_unparsed_source_does_not_claim_zero_functions_or_classes(tmp_path):
    write(tmp_path, "broken.py", "def function_that_cannot_parse(:\n")
    app = Application(tmp_path)
    app.scan()
    broken = modules(app)["broken.py"]
    assert broken.data["parse_status"] == "error"
    assert "function_count" not in broken.data and "class_count" not in broken.data


def test_fact_upgrade_preserves_an_explicitly_saved_annotation(tmp_path, monkeypatch):
    version, parser = discovery.INDEX_VERSION, indexing.parse_module

    def legacy(*args):
        parsed, node, error = parser(*args)
        if node.data.get("test_basis"):
            data = {key: value for key, value in node.data.items() if key != "test_basis"}
            data["is_test"] = False
            node = replace(node, data=data)
            for symbol in parsed.nodes:
                symbol.data["is_test"] = False
        return parsed, node, error

    monkeypatch.setattr(discovery, "INDEX_VERSION", "facts-v8-binary-inputs")
    monkeypatch.setattr(indexing, "parse_module", legacy)
    old = project(tmp_path)
    assert not modules(old)["checks/case.py"].data["is_test"]
    run = WorkflowRunner(old, lambda request, *_a, **_k: answer(request))
    assert run.start("mapping", scope=["checks/case.py"], background=False)["status"] == "completed"
    saved = modules(old)["checks/case.py"].data["annotation"]
    monkeypatch.setattr(discovery, "INDEX_VERSION", version)
    monkeypatch.setattr(indexing, "parse_module", parser)
    new = Application(tmp_path)
    new.scan()
    restored = modules(new)["checks/case.py"]
    assert restored.data["is_test"]
    assert restored.data["annotation_status"] == "current"
    assert restored.data["annotation"] == saved


@pytest.mark.parametrize("scope", [["."], [".", "checks", "tests"], ["./", "./checks", "./tests"]])
def test_inferred_test_role_keeps_dynamic_runtime_consumers_of_retained_values(tmp_path, scope):
    root = tmp_path / "project"
    root.mkdir()
    write(root, "billing.py", "LIMIT = 1\n")
    write(root, "conftest.py", "def account():\n    return 1\n")
    write(
        root,
        "checks/case.py",
        "from conftest import account\nfrom billing import LIMIT\n"
        "def helper():\n    return LIMIT\n"
        "def test_charge_once():\n    assert helper() == 1\n",
    )
    write(
        root,
        "tests/test_direct.py",
        "from checks.case import helper\ndef test_direct():\n    assert helper() == 1\n",
    )
    write(
        root,
        "tests/test_dynamic.py",
        "import importlib\ndef test_dynamic():\n"
        "    assert importlib.import_module('checks.case').helper() == 1\n",
    )
    app = Application(root)
    manifest = app.start_recording("unit")["manifest"]
    coverage, junit, reads = (
        tmp_path / name for name in ("unit.coverage", "unit.xml", "reads.json")
    )
    environment = os.environ | {
        "PYTHONPATH": str(Path(recorder.__file__).parent),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "ANAXIGRAPH_ROOT": str(root),
        "ANAXIGRAPH_RECORDING": manifest,
        "ANAXIGRAPH_READS": str(reads),
        "ANAXIGRAPH_TRACE_CHILDREN": "0",
        "COVERAGE_FILE": str(coverage),
    }
    command = [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]
    recorded = subprocess.run(
        command
        + [
            "-p",
            "pytest_cov",
            "-p",
            "anaxigraph_reads",
            "--cov=billing",
            "--cov=checks",
            "--cov-context=test",
            "--cov-report=",
            f"--junitxml={junit}",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    app.record_tests(
        coverage,
        "unit",
        scope,
        recorded_root=str(root),
        junit=[junit],
        reads=reads,
    )
    record = json.loads((root / ".anaxigraph-v2/test-records/unit.json").read_text())
    assert record["scope"][0] == "" and "billing.py" in record["imports"]
    assert modules(app)["checks/case.py"].data["is_test"] is True
    write(root, "billing.py", "LIMIT = 2\n")
    [selection] = app.tests_for_change()["suites"]
    assert selection["status"] == "selected", selection
    assert selection["test_files"] == ["tests/test_direct.py", "tests/test_dynamic.py"]
    mutant_report = tmp_path / "mutant.xml"
    clean = {key: value for key, value in environment.items() if not key.startswith("ANAXIGRAPH_")}
    mutant = subprocess.run(
        command + [f"--junitxml={mutant_report}"],
        cwd=root,
        env=clean,
        capture_output=True,
        text=True,
    )
    assert mutant.returncode == 1, mutant.stdout + mutant.stderr
    failures = [
        case.get("name")
        for case in ElementTree.parse(mutant_report).getroot().iter("testcase")
        if case.find("failure") is not None
    ]
    assert sorted(failures) == ["test_direct", "test_dynamic"]
