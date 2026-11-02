"""Real qualification collection plus refusal of unknown or changed evidence."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_evidence as collector  # noqa: E402
import metrics  # noqa: E402

from anaxigraph.analysis.ci_obligations import REPORT_PATH, audit_selection  # noqa: E402
from anaxigraph.indexing import IndexSession  # noqa: E402

TEST = "tests/test_lib.py::test_a"


@pytest.fixture
def candidate(tmp_path):
    repo = tmp_path / "candidate"
    repo.mkdir()
    (repo / "lib.py").write_text("def increment(x):\n    return x + 1\n")
    (repo / "tests").mkdir()
    (repo / "tests/test_lib.py").write_text(
        "from lib import increment\n"
        "def test_a():\n    assert increment(0) == 1\n"
        "def test_b():\n    assert increment(1) == 2\n"
        "def test_c():\n    assert increment(2) == 3\n"
    )
    return repo


def observed(monkeypatch):
    monkeypatch.setattr(collector, "environment", lambda repo: {"python": "synthetic-test-only"})
    monkeypatch.setattr(metrics, "collect", lambda repo, targets: [TEST])
    monkeypatch.setattr(metrics, "select", lambda repo, paths: (["ALL"], 0.0))
    monkeypatch.setattr(
        metrics, "coverage_by_test", lambda repo: ({"lib.py": {2: {TEST}}}, {"returncode": 0})
    )


def test_real_cli_report_observes_omissions_and_measures_supplied_change(candidate):
    interpreter = Path(os.environ.get("COLLECT_EVIDENCE_TEST_PYTHON", sys.executable)).absolute()
    ready = subprocess.run([str(interpreter), "-c", "import pytest_cov"], capture_output=True)
    if ready.returncode:
        pytest.skip("Set COLLECT_EVIDENCE_TEST_PYTHON to a venv interpreter with pytest-cov")
    (candidate / ".venv").symlink_to(interpreter.parent.parent, target_is_directory=True)
    (candidate / "selector.py").write_text(
        "import sys\nsys.stdin.read()\nprint('tests/test_lib.py::test_a')\n"
    )
    (candidate / "ci.json").write_text(json.dumps({"select": [".venv/bin/python", "selector.py"]}))
    output = candidate / REPORT_PATH
    command = [
        sys.executable,
        str(Path(collector.__file__)),
        "--repo",
        str(candidate),
        "--output",
        str(output),
        "--measure-diff",
        "--changed",
        "lib.py",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    value = json.loads(output.read_text())
    snapshot = IndexSession(candidate).snapshot()
    audited = audit_selection(value, snapshot.files, collector.environment(candidate))
    assert audited["status"] == "reported_gap"
    assert audited["omitted_execution_pairs"] == 2
    assert value["inputs"] == {p: f for p, f in snapshot.files.items() if p != REPORT_PATH}
    assert value["tests"] == [f"tests/test_lib.py::test_{suffix}" for suffix in "abc"]
    [library] = [row for row in value["files"] if row["path"] == "lib.py"]
    assert library["executing"] == [0, 1, 2] and library["selected"] == [0]
    assert value["faults"] == []
    measurement = value["measurement"]
    assert measurement["full_passed"] and measurement["selected_passed"]
    assert measurement["tests_selected"] == 1 and measurement["tests_total"] == 3
    assert measurement["full_seconds"] > 0 and measurement["selected_seconds"] > 0
    assert len(value["environment"]["distributions_sha256"]) == 64
    refreshed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert refreshed.returncode == 0, refreshed.stderr
    assert json.loads(output.read_text())["inputs"] == value["inputs"]
    assert list(output.parent.iterdir()) == [output]


@pytest.mark.parametrize("coverage,code", [({}, 0), ({"lib.py": {2: {TEST}}}, 1)])
def test_missing_or_failed_coverage_never_becomes_zero_filled_report(
    candidate, monkeypatch, coverage, code
):
    observed(monkeypatch)
    monkeypatch.setattr(metrics, "coverage_by_test", lambda repo: (coverage, {"returncode": code}))
    output = candidate.parent / "report.json"
    with pytest.raises(ValueError, match="coverage data"):
        collector.write_report(candidate, output)
    assert not output.exists()


def test_partial_collection_failure_is_not_accepted_as_complete(candidate, monkeypatch):
    monkeypatch.setattr(
        metrics, "run", lambda *a, **kw: subprocess.CompletedProcess([], 2, TEST, "error")
    )
    assert metrics.collect(candidate, ["ALL"]) is None


def test_selector_failure_refuses_report(candidate, monkeypatch):
    observed(monkeypatch)
    monkeypatch.setattr(metrics, "select", lambda repo, paths: (None, 0))
    with pytest.raises(ValueError, match="Selector"):
        collector.report(candidate)


def test_changed_inputs_and_environment_refuse_report(candidate, monkeypatch):
    observed(monkeypatch)

    def mutate(repo, paths):
        (repo / "lib.py").write_text("changed = True\n")
        return ["ALL"], 0

    monkeypatch.setattr(metrics, "select", mutate)
    with pytest.raises(ValueError, match="changed during"):
        collector.report(candidate)
    observed(monkeypatch)
    contexts = iter([{"python": "before"}, {"python": "after"}])
    monkeypatch.setattr(collector, "environment", lambda repo: next(contexts))
    with pytest.raises(ValueError, match="changed during"):
        collector.report(candidate)


def test_output_limits_atomic_no_overwrite_and_symlinks(candidate, monkeypatch):
    output = candidate.parent / "report.json"
    monkeypatch.setattr(
        collector, "report", lambda repo, changed: {"test": "x" * collector.MAX_BYTES}
    )
    with pytest.raises(ValueError, match="1 MB"):
        collector.write_report(candidate, output)
    assert not output.exists()
    monkeypatch.setattr(collector, "report", lambda repo, changed: {"test": "bounded"})
    collector.write_report(candidate, output)
    with pytest.raises(FileExistsError, match="overwrite"):
        collector.write_report(candidate, output)
    assert json.loads(output.read_text()) == {"test": "bounded"}
    with pytest.raises(ValueError, match="In-repository output"):
        collector.write_report(candidate, candidate / "other.json")
    link = candidate.parent / "link"
    link.symlink_to(candidate, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        collector.write_report(candidate, link / "report.json")
    with pytest.raises(ValueError, match="Symlink"):
        collector.write_report(link, candidate.parent / "new.json")


def test_empty_selection_measurement_executes_no_selected_assertions(candidate, monkeypatch):
    monkeypatch.setattr(metrics, "select", lambda repo, paths: ([], 0))
    calls = []

    def run(repo, targets):
        calls.append(targets)
        return True, 0.25

    monkeypatch.setattr(metrics, "run_tests", run)
    measured = collector.measure_diff(candidate, ["lib.py"], [TEST])
    assert calls == [["ALL"]]
    assert measured["selected_passed"] is None and measured["selected_seconds"] == 0
    assert measured["tests_selected"] == 0


def test_unknown_coverage_test_id_refuses_report(candidate, monkeypatch):
    observed(monkeypatch)
    monkeypatch.setattr(
        metrics,
        "coverage_by_test",
        lambda repo: ({"lib.py": {2: {"tests/test_unknown.py::test_missing"}}}, {"returncode": 0}),
    )
    with pytest.raises(ValueError, match="was not collected"):
        collector.report(candidate)


def test_change_measurement_flags_require_each_other(candidate):
    output = candidate.parent / "report.json"
    command = [
        sys.executable,
        str(Path(collector.__file__)),
        "--repo",
        str(candidate),
        "--output",
        str(output),
        "--changed",
        "lib.py",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and "--measure-diff" in result.stderr
    assert not output.exists()


def test_long_valid_node_id_survives_complete_producer_pipeline(candidate, monkeypatch):
    observed(monkeypatch)
    identity = "tests/test_lib.py::test_a[" + "long-parameter" * 400 + "]"
    monkeypatch.setattr(metrics, "collect", lambda repo, targets: [identity])
    monkeypatch.setattr(
        metrics, "coverage_by_test", lambda repo: ({"lib.py": {2: {identity}}}, {"returncode": 0})
    )
    output = candidate.parent / "long-id-report.json"
    collector.write_report(candidate, output)
    value = json.loads(output.read_text())
    assert value["tests"] == [identity] and value["files"][0]["executing"] == [0]
    assert (
        audit_selection(value, collector.inventory(candidate), value["environment"])["status"]
        == "reported_passed"
    )


def test_candidate_src_precedes_borrowed_editable_and_inherited_packages(candidate, monkeypatch):
    package = candidate / "src/anaxigraph"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("origin = 'candidate-src'\n")
    inherited = candidate.parent / "inherited"
    (inherited / "anaxigraph").mkdir(parents=True)
    (inherited / "anaxigraph/__init__.py").write_text("origin = 'inherited-conflict'\n")
    monkeypatch.setenv("PYTHONPATH", str(inherited))
    env = metrics.candidate_env(candidate)
    assert env["PYTHONPATH"].split(os.pathsep) == [
        str(candidate),
        str(candidate / "src"),
        str(inherited),
    ]
    # The test interpreter also has this project's editable anaxigraph installed.
    result = metrics.run(
        [sys.executable, "-c", "import anaxigraph; print(anaxigraph.origin)"], candidate, 10, env
    )
    assert result.returncode == 0 and result.stdout.strip() == "candidate-src"


def test_canonical_refresh_keeps_last_valid_report_on_failure(candidate, monkeypatch):
    output = candidate / REPORT_PATH
    monkeypatch.setattr(collector, "report", lambda repo, changed: {"generation": 1})
    collector.write_report(candidate, output)
    monkeypatch.setattr(collector, "report", lambda repo, changed: {"generation": 2})
    collector.write_report(candidate, output)
    last_valid = output.read_bytes()
    assert json.loads(last_valid) == {"generation": 2}

    def failed(repo, changed):
        raise ValueError("failed source evidence")

    monkeypatch.setattr(collector, "report", failed)
    with pytest.raises(ValueError, match="failed source evidence"):
        collector.write_report(candidate, output)
    monkeypatch.setattr(
        collector, "report", lambda repo, changed: {"oversized": "x" * collector.MAX_BYTES}
    )
    with pytest.raises(ValueError, match="1 MB"):
        collector.write_report(candidate, output)
    assert output.read_bytes() == last_valid
    assert list(output.parent.iterdir()) == [output]
