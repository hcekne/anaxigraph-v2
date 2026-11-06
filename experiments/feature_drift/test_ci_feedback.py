"""Executable qualification of consumer feedback and bounded repair, without models."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_evidence as collector  # noqa: E402
import metrics  # noqa: E402
import runner  # noqa: E402


def test_empty_selection_executes_no_tests_and_execution_errors_are_not_detected_faults(
    monkeypatch, tmp_path
):
    def forbidden(*args, **kwargs):
        raise AssertionError("Empty selection executed the default full suite")

    monkeypatch.setattr(metrics, "run", forbidden)
    assert metrics.run_tests(tmp_path, []) == (True, 0.0)
    for code in (2, 5, 124):
        monkeypatch.setattr(
            metrics, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], code, "", "")
        )
        assert metrics.run_tests(tmp_path, ["tests/missing.py"])[0] is None


def test_complete_single_file_selections_do_not_hide_multifile_omission(monkeypatch, tmp_path):
    (tmp_path / "ci.json").write_text("{}")
    tests = ["tests/test_a.py::test_a", "tests/test_b.py::test_b"]
    monkeypatch.setattr(metrics, "production_files", lambda repo: ["a.py", "b.py"])
    monkeypatch.setattr(
        metrics, "collect", lambda repo, targets: tests if targets == ["ALL"] else targets
    )
    monkeypatch.setattr(
        metrics,
        "select",
        lambda repo, paths: ([tests[0]] if paths != ["b.py"] else [tests[1]], 0.0),
    )
    monkeypatch.setattr(
        metrics,
        "coverage_by_test",
        lambda repo: ({"a.py": {1: {tests[0]}}, "b.py": {1: {tests[1]}}}, {"returncode": 0}),
    )
    monkeypatch.setattr(
        metrics, "plant_bugs", lambda *args: {"caught_by_suite": 1, "missed_by_selection": 0}
    )
    result = metrics.ci_quality(tmp_path, 1, changed_paths=["a.py", "b.py"])
    assert result["executing_recall"] == 1
    assert result["change_selection"]["status"] == "failed"
    assert result["change_selection"]["omitted_tests"] == [tests[1]]


@pytest.fixture
def real_candidate(tmp_path):
    root = tmp_path / "candidate"
    root.mkdir()
    (root / ".venv").symlink_to(
        Path(sys.executable).absolute().parent.parent, target_is_directory=True
    )
    (root / "lib.py").write_text("def increment(value):\n    return value + 1\n")
    (root / "tests").mkdir()
    for name, value in (("unit", 1), ("consumer", 8)):
        (root / f"tests/test_{name}.py").write_text(
            f"from lib import increment\ndef test_{name}():\n    assert increment({value}) == {value + 1}\n"
        )
    (root / "selector.py").write_text("import sys\nsys.stdin.read()\nprint('tests/test_unit.py')\n")
    (root / "ci.json").write_text(json.dumps({"select": [".venv/bin/python", "selector.py"]}))
    return root


def test_real_coverage_gap_repair_refreshes_evidence_and_preserves_initial_failure(
    real_candidate, tmp_path, monkeypatch
):
    root = real_candidate
    coverage_calls = []
    original = metrics.coverage_by_test

    def measured(repo):
        coverage_calls.append(repo)
        return original(repo)

    monkeypatch.setattr(metrics, "coverage_by_test", measured)
    first = collector.quality_report(
        root, root / collector.REPORT_PATH, seed=1, mutants=1, changed=["lib.py"]
    )
    assert len(coverage_calls) == 1
    assert first["report"] is not None, first["error"]
    assert runner.ci_gate(first)["status"] == "failed"
    assert first["ci"]["omitted_execution_pairs"] == 1
    frozen = copy.deepcopy(first)
    (root / "selector.py").write_text("import sys\nsys.stdin.read()\nprint('ALL')\n")
    assert (
        collector.audit_selection(
            first["report"], collector.inventory(root), first["report"]["environment"]
        )["status"]
        == "stale"
    )
    second = collector.quality_report(
        root, root / collector.REPORT_PATH, seed=1, mutants=1, changed=["lib.py", "selector.py"]
    )
    assert len(coverage_calls) == 2
    assert second["report"] is not None, second["error"]
    assert runner.ci_gate(second)["status"] == "passed"
    assert second["ci"]["executing_recall"] == 1
    assert second["ci"]["mutants"]["caught_by_suite"] > 0
    assert json.loads((root / collector.REPORT_PATH).read_text()) == second["report"]
    assert first == frozen


def test_repair_loop_uses_original_baseline_and_accounts_every_session(monkeypatch, tmp_path):
    config = SimpleNamespace(ci_repair_attempts=2, agent="synthetic-transport")
    seen = []

    def verify(config, repo, out, step, base, head, **kwargs):
        seen.append((base, kwargs["baseline_id"], kwargs["baseline_inputs"]))
        status = "failed" if len(seen) == 1 else "passed"
        return {
            "evaluation": {"own_suite": {}, "hidden": {}, "ci": {}},
            "gate": {"status": status},
            "feedback": {"gate": {"status": status}},
            "verification_seconds": 3,
            "anaxigraph_seconds": 2,
        }

    monkeypatch.setattr(runner, "safety_feedback", verify)
    prompts = []

    def agent(*args):
        prompts.append(args[3])
        return {
            "status": "completed",
            "seconds": 7,
            "usage": {"input_tokens": 17, "cached_input_tokens": 2, "output_tokens": 5},
        }

    monkeypatch.setattr(runner, "run_agent", agent)
    monkeypatch.setattr(runner, "commit", lambda *args: "repair-head")
    initial = {
        "status": "completed",
        "seconds": 5,
        "usage": {"input_tokens": 11, "cached_input_tokens": 1, "output_tokens": 3},
    }
    result = runner.repair_ci(
        config,
        tmp_path,
        tmp_path / "attempts",
        "original task",
        initial,
        13,
        "original-git-base",
        "initial-head",
        baseline_id="original-snapshot",
        baseline_inputs={"lib.py": "original"},
        treatment=True,
    )
    assert seen == [("original-git-base", "original-snapshot", {"lib.py": "original"})] * 2
    assert result["agent"]["seconds"] == 12
    assert result["agent"]["usage"] == {
        "input_tokens": 28,
        "cached_input_tokens": 3,
        "output_tokens": 8,
    }
    assert result["verification_seconds"] == 6 and result["ci_validation_seconds"] == 4
    assert (
        result["initial_ci_gate"]["status"] == "failed" and result["ci_gate"]["status"] == "passed"
    )
    assert "original-snapshot" in prompts[0] and "Do not prepare a new baseline" in prompts[0]
    assert (
        json.loads((tmp_path / "attempts/attempt-0.json").read_text())["commit"] == "initial-head"
    )


@pytest.mark.parametrize("arm", ["bare", "charter", "anaxigraph"])
def test_each_arm_stops_at_same_bounded_unqualified_gate(monkeypatch, tmp_path, arm):
    config = SimpleNamespace(ci_repair_attempts=2, agent="synthetic-transport")
    calls = []

    def verify(*args, **kwargs):
        return {
            "evaluation": {"ci": {}},
            "gate": {"status": "incomplete"},
            "feedback": {"gate": {"status": "incomplete"}},
            "verification_seconds": 1,
            "anaxigraph_seconds": 0,
        }

    monkeypatch.setattr(runner, "safety_feedback", verify)
    monkeypatch.setattr(runner, "run_agent", lambda *args: calls.append(args) or {"seconds": 1})
    monkeypatch.setattr(runner, "commit", lambda *args: "head")
    result = runner.repair_ci(
        config,
        tmp_path,
        tmp_path / arm,
        "task",
        {"seconds": 1},
        13,
        "base",
        "head",
        treatment=arm == "anaxigraph",
    )
    assert len(calls) == 2 and len(result["ci_attempts"]) == 3
    assert result["ci_gate"]["status"] == "incomplete"
    assert result["agent"]["seconds"] == 3


def test_blocked_chain_never_marks_request_done_or_spends_more_on_resume(monkeypatch, tmp_path):
    chain = tmp_path / "bare-r0"
    (chain / "repo").mkdir(parents=True)
    (chain / "pending-step-13.json").write_text('{"step":13}')
    config = SimpleNamespace(state=tmp_path)
    monkeypatch.setattr(runner, "validate_resume", lambda *args: None)
    monkeypatch.setattr(
        runner, "run_agent", lambda *args: pytest.fail("Blocked resume started another session")
    )
    result = runner.run_chain(config, "bare", 0)
    assert result["status"] == "blocked"
    assert not (chain / "steps.jsonl").exists()


def test_partial_junit_does_not_hide_a_collection_error(monkeypatch, tmp_path):
    output = tmp_path / "partial.xml"
    output.write_text('<testsuite><testcase classname="step_01" name="test_one"/></testsuite>')
    monkeypatch.setattr(
        runner,
        "sh",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 2, "", "collection failed"),
    )
    result = runner.hidden_tests(tmp_path, ["step_01.py", "step_02.py"], 2, output)
    assert result["passed"] and result["collection_error"]


@pytest.mark.parametrize(
    "broken, allowed, status",
    [
        ("partial", None, "failed"),
        ("probe", None, "passed"),
        ("probe", ["probe_2::test_probe"], "passed"),
        ("probe", [], "failed"),
    ],
)
def test_feedback_requires_every_module_and_gates_only_new_behavioral_failures(
    monkeypatch, tmp_path, broken, allowed, status
):
    """Original misses are scored, not gated; a repair may not add a hidden failure."""
    inputs, environment = {"lib.py": "frozen"}, {"python": "synthetic qualification"}
    report = {
        "version": 1,
        "inputs": inputs,
        "environment": environment,
        "tests": ["tests/test_lib.py::test_one"],
        "files": [{"path": "lib.py", "executing": [0], "selected": [0]}],
        "faults": [{"path": "lib.py", "reference_detected": True, "selection_detected": True}],
        "suite": {"returncode": 0, "tests": 1, "passed": 1},
    }
    collected = {
        "report": report,
        "ci": {
            "change_selection": {"status": "passed"},
            "coverage_run": report["suite"],
            "mutants": {"caught_by_suite": 1},
        },
        "error": None,
    }
    monkeypatch.setattr(
        collector, "quality_report", lambda *args, **kwargs: copy.deepcopy(collected)
    )
    monkeypatch.setattr(collector, "inventory", lambda *args: inputs)
    monkeypatch.setattr(collector, "environment", lambda *args: environment)
    monkeypatch.setattr(metrics, "diff_stats", lambda *args: {"changed_files": ["lib.py"]})
    monkeypatch.setattr(metrics, "static_metrics", lambda *args: {})
    seen = []

    def hidden(repo, files, *args):
        seen.append(files)
        by_file = {"step_01": {"passed": 1, "total": 1}}
        if broken == "probe":
            by_file.update(step_02={"passed": 1, "total": 1}, probe_2={"passed": 0, "total": 1})
        failed = ["probe_2::test_probe"] if broken == "probe" else []
        return {"by_file": by_file, "failed": failed, "collection_error": False}

    monkeypatch.setattr(runner, "hidden_tests", hidden)
    files = ["step_01.py", "step_02.py", "probe_2.py"]
    config = SimpleNamespace(mutants=1, scenario={"steps": [1, 2]})
    result = runner.safety_feedback(
        config,
        tmp_path,
        tmp_path / "checks",
        2,
        "base",
        "head",
        acceptance_files=files,
        allowed_failures=allowed,
    )
    assert result["gate"]["status"] == status
    assert seen == [files]


def test_real_cli_validation_sees_published_evidence_and_keeps_original_baseline(
    real_candidate, tmp_path
):
    from anaxigraph.application import Application

    root = real_candidate
    (root / "selector.py").write_text("import sys\nsys.stdin.read()\nprint('ALL')\n")
    app = Application(root)
    app.scan()
    baseline = app.prepare_change("Clarify the increment behavior documentation")["baseline_id"]
    library = root / "lib.py"
    library.write_text(library.read_text() + "\n# Documented change\n")
    collected = collector.quality_report(
        root, root / collector.REPORT_PATH, seed=1, mutants=1, changed=["lib.py"]
    )
    assert collected["report"] is not None, collected["error"]
    config = SimpleNamespace(runtime=Path(__file__).resolve().parents[2] / ".venv/bin/anaxigraph")
    first = runner.fresh_validation(config, root, tmp_path / "validation-one", baseline, collected)
    assert first.get("completion_allowed") is True, first
    assert first["baseline_id"] == baseline
    assert first["ci_selection"]["ready"]
    library.write_text("def increment(value):\n    return 0\n")
    second = runner.fresh_validation(config, root, tmp_path / "validation-two", baseline, collected)
    assert second.get("completion_allowed") is False
    assert second["baseline_id"] == baseline
    assert second["ci_selection"]["status"] == "stale"


@pytest.mark.parametrize(
    "tamper", ["replace", "remove", "filter", "add", "selector", "data", "stale"]
)
def test_ci_only_repair_retains_verification_bytes_and_ids(monkeypatch, tmp_path, tamper):
    (tmp_path / "tests").mkdir()
    existing = tmp_path / "tests/test_existing.py"
    existing.write_text("def test_existing():\n    assert 1 == 1\n")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/test_rooms.py").write_text("ROOMS = {}\n")
    (tmp_path / "tests/room.json").write_text("{}")
    original = "tests/test_existing.py::test_existing"
    config = SimpleNamespace(ci_repair_attempts=1, agent="synthetic-transport")
    calls = []

    def verify(*args, **kwargs):
        calls.append(True)
        ids = [] if tamper in {"remove", "filter"} and len(calls) > 1 else [original]
        failing = ["tests/test_existing.py"] if tamper == "stale" else []
        return {
            "evaluation": {"ci": {"coverage_run": {"failed_files": failing}}},
            "gate": {"status": "failed" if len(calls) == 1 else "passed"},
            "feedback": {},
            "verification_seconds": 1,
            "anaxigraph_seconds": 0,
            "test_inventory": ids,
        }

    def repair(*args):
        if tamper in {"replace", "stale"}:
            existing.write_text("def test_existing():\n    pass\n")
        elif tamper == "selector":
            (tmp_path / "tools/test_rooms.py").write_text("ROOMS = {'a': 1}\n")
        elif tamper == "data":
            (tmp_path / "tests/room.json").write_text('{"a": 1}')
        elif tamper == "remove":
            existing.unlink()
        elif tamper == "filter":
            (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -k new\n")
        else:
            (tmp_path / "tests/test_new.py").write_text("def test_new():\n    assert True\n")
        return {"seconds": 1}

    monkeypatch.setattr(runner, "safety_feedback", verify)
    monkeypatch.setattr(runner, "run_agent", repair)
    monkeypatch.setattr(runner, "commit", lambda *args: "repair")
    result = runner.repair_ci(
        config, tmp_path, tmp_path / "logs", "task", {"seconds": 1}, 13, "base", "head"
    )
    allowed = {"add", "selector", "data", "stale"}
    assert result["ci_gate"]["status"] == ("passed" if tamper in allowed else "failed")


def test_standalone_check_exits_nonzero_for_omissions_then_passes_after_repair(real_candidate):
    root = real_candidate
    output = root / collector.REPORT_PATH
    command = [
        sys.executable,
        collector.__file__,
        "--repo",
        str(root),
        "--output",
        str(output),
        "--changed",
        "lib.py",
        "--check",
    ]
    failed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert failed.returncode == 1, failed.stderr
    result = json.loads(failed.stdout)
    assert result["qualification"]["status"] == "reported_gap"
    initial = json.loads(output.read_text())
    assert initial["suite"]["tests"] == initial["suite"]["passed"] == 2
    assert "measurement" not in initial and initial["change_selection"]["changed_paths"] == [
        "lib.py"
    ]
    (root / "selector.py").write_text("import sys\nsys.stdin.read()\nprint('ALL')\n")
    repaired = subprocess.run(
        [*command, "--changed", "selector.py"], capture_output=True, text=True, timeout=60
    )
    assert repaired.returncode == 0, repaired.stderr
    assert json.loads(repaired.stdout)["qualification"]["status"] == "reported_passed"
    updated = json.loads(output.read_text())
    assert updated["inputs"] != initial["inputs"] and "measurement" not in updated
    last_valid = output.read_bytes()
    (root / "ci.json").unlink()
    refused = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert refused.returncode == 1 and "Selector" in refused.stderr
    assert output.read_bytes() == last_valid


def test_real_git_backed_feedback_loop_repairs_consumer_gap_without_models(
    real_candidate, tmp_path, monkeypatch
):
    from anaxigraph.application import Application
    from anaxigraph.indexing import IndexSession

    root = real_candidate
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.name", "Fixture"),
        ("config", "user.email", "fixture@example.test"),
    ):
        runner.sh(["git", "-C", str(root), *args])
    (root / ".git/info/exclude").write_text("\n".join(runner.EXCLUDE) + "\n")
    base = runner.commit(root, "Original product")
    app = Application(root)
    app.scan()
    baseline = app.prepare_change("Clarify increment documentation")["baseline_id"]
    baseline_inputs = collector.inventory(root)
    (root / "lib.py").write_text((root / "lib.py").read_text() + "\n# Clarified documentation\n")
    head = runner.commit(root, "Feature edit")
    oracle = tmp_path / "oracle"
    oracle.mkdir()
    (oracle / "pytest.ini").write_text("[pytest]\n")
    (oracle / "step_13.py").write_text(
        "import os,sys\nsys.path.insert(0,os.environ['DRIFT_CANDIDATE'])\n"
        "from lib import increment\ndef test_held_out_input():\n    assert increment(100) == 101\n"
    )
    monkeypatch.setattr(runner, "HIDDEN", oracle)
    calls = []

    def coding_transport(*args):
        calls.append(args[3])
        (root / "selector.py").write_text("import sys\nsys.stdin.read()\nprint('ALL')\n")
        return {
            "status": "completed",
            "seconds": 0,
            "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
            "evidence_kind": "synthetic-coding-transport",
        }

    monkeypatch.setattr(runner, "run_agent", coding_transport)
    config = SimpleNamespace(
        ci_repair_attempts=1,
        agent="synthetic-transport",
        mutants=1,
        scenario={"steps": [13]},
        runtime=Path(__file__).resolve().parents[2] / ".venv/bin/anaxigraph",
    )
    initial = {
        "status": "completed",
        "seconds": 0,
        "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
    }
    result = runner.repair_ci(
        config,
        root,
        tmp_path / "feedback",
        "Clarify increment documentation",
        initial,
        13,
        base,
        head,
        baseline_id=baseline,
        baseline_inputs=baseline_inputs,
        treatment=True,
    )
    assert result["initial_ci_gate"]["status"] == "failed"
    assert result["ci_gate"]["status"] == "passed", result["ci_attempts"][-1]["feedback"]
    assert len(calls) == 1 and len(result["ci_attempts"]) == 2
    assert result["ci_attempts"][0]["feedback"]["validation"]["completion_allowed"] is False
    assert result["ci_attempts"][1]["feedback"]["validation"]["completion_allowed"] is True
    assert all(
        attempt["feedback"]["validation"]["baseline_id"] == baseline
        for attempt in result["ci_attempts"]
    )
    assert collector.REPORT_PATH in IndexSession(root).snapshot().files


def test_unstable_parametrised_ids_and_conftest_obligations_are_measured(real_candidate):
    root = real_candidate
    (root / "tests/test_unstable.py").write_text(
        "import time\nimport pytest\nfrom lib import increment\n"
        "@pytest.mark.parametrize('v', [1, 2], ids=lambda v: f'{v}-{time.time_ns()}')\n"
        "def test_varies(v):\n    assert increment(v) == v + 1\n"
    )
    (root / "tests/conftest.py").write_text("import pytest\n")
    quality = metrics.ci_quality(root, 1, 2, changed_paths=["tests/conftest.py"])
    assert quality["collection_complete"] is True
    assert quality["unstable_test_ids"]["functions"] == ["tests/test_unstable.py::test_varies"]
    assert quality["mutants"]["unverified"] == 0
    omitted = quality["change_selection"]["omitted_tests"]
    assert "tests/test_consumer.py::test_consumer" in omitted
    assert quality["change_selection"]["status"] == "failed"


def test_failing_own_tests_are_named_with_their_module(real_candidate):
    root = real_candidate
    (root / "tests/test_stale.py").write_text(
        "from lib import increment\nclass TestOld:\n"
        "    def test_old(self):\n        assert increment(1) == 3\n"
    )
    _, info = metrics.coverage_by_test(root)
    assert info["failed_files"] == ["tests/test_stale.py"]
    assert info["failed_tests"] == ["tests.test_stale.TestOld::test_old"]
