"""The treatment must actually finish before its coding session starts."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preparation  # noqa: E402
import runner  # noqa: E402


def completed(stages):
    return {
        "status": "completed",
        "failed_batches": 0,
        "active_workers": 0,
        "completed_batches": 1,
        "total_batches": 1,
        "stages_planned": stages,
        "reconciliation": {},
    }


def test_patterns_cannot_pass_on_mapping_only_or_cli_success():
    result = completed(["annotate"])
    assert not preparation.qualified("patterns", result, result)
    result["stages_planned"] += ["pattern-screen", "patterns", "architecture", "delivery"]
    assert preparation.qualified("patterns", result, result)
    for patch in (
        {"status": "failed"},
        {"failed_batches": 1},
        {"active_workers": 1},
        {"export_error": "disk full"},
        {"completed_batches": 0},
    ):
        assert not preparation.qualified("patterns", result | patch, result)


def test_resume_keeps_finished_work_and_all_attempt_costs(tmp_path):
    run_id = "a" * 32
    saved = tmp_path / ".anaxigraph-v2/workflows" / f"{run_id}.json"
    saved.parent.mkdir(parents=True)
    calls = []

    def runtime(_, repo, *args):
        calls.append(args)
        if args == ("scan",):
            return {"snapshot": {"id": "same"}}
        result = completed(["annotate"]) | {"run_id": run_id}
        if args[0] == "run":
            result.update(status="failed", failed_batches=1)
        saved.write_text(json.dumps(result))
        return result

    config = SimpleNamespace(workflow_config=None, review_steps=set(), review_kinds=("patterns",))
    out = tmp_path / "receipts"
    first = preparation.refresh(
        config, tmp_path, 0, out, runtime, runner.workflow_summary, runner.write_json
    )
    assert not first["qualified"]
    second = preparation.refresh(
        config, tmp_path, 0, out, runtime, runner.workflow_summary, runner.write_json
    )
    assert second["qualified"] and ("resume", run_id) in calls
    assert calls.count(("run", "mapping")) == 1
    third = preparation.refresh(
        config, tmp_path, 0, out, runtime, runner.workflow_summary, runner.write_json
    )
    assert third["qualified"] and calls.count(("resume", run_id)) == 1
    receipts = [json.loads(path.read_text()) for path in sorted(out.glob("attempt-*.json"))]
    assert second["seconds"] >= first["seconds"]
    assert third["seconds"] == pytest.approx(sum(x["seconds"] for x in receipts))
    assert len(receipts) == 5 and receipts[1]["workflow"]["status"] == "failed"


def test_interrupted_receipt_is_retained_without_starting_another_worker(tmp_path):
    runner.write_json(tmp_path / "attempt-000.json", {"started": 1})
    result = preparation.refresh(
        None, None, 0, tmp_path, lambda *a: pytest.fail("Started duplicate work"), None, None
    )
    assert not result["qualified"] and "Interrupted" in result["error"]
    assert json.loads((tmp_path / "attempt-000.json").read_text()) == {"started": 1}


def test_finished_receipt_without_manifest_does_not_repeat_paid_work(tmp_path):
    runner.write_json(tmp_path / "attempt-000.json", {"started": 1, "finished": 2, "seconds": 1.0})
    result = preparation.refresh(
        None,
        None,
        0,
        tmp_path,
        lambda *a: pytest.fail("Repeated retained work"),
        None,
        None,
    )
    assert not result["qualified"] and "mismatch" in result["error"]
    assert result["retained_attempt_seconds"] == 1.0


def test_timed_out_transport_does_not_start_a_second_unknown_worker(tmp_path):
    calls = []

    def runtime(_, repo, *args):
        calls.append(args)
        if args == ("scan",):
            return {"snapshot": {"id": "same"}}
        raise subprocess.TimeoutExpired(args, 7200)

    config = SimpleNamespace(workflow_config=None, review_steps=set(), review_kinds=("patterns",))
    first = preparation.refresh(
        config, tmp_path, 0, tmp_path, runtime, runner.workflow_summary, runner.write_json
    )
    assert not first["qualified"]
    second = preparation.refresh(
        config, tmp_path, 0, tmp_path, runtime, runner.workflow_summary, runner.write_json
    )
    assert not second["qualified"] and "lifetime is unknown" in second["error"]
    assert calls == [("scan",), ("run", "mapping")]


@pytest.mark.skipif(sys.platform != "linux", reason="Model-free CLI probe uses system PATH")
def test_real_cli_failed_mapping_is_retained_and_resumed_without_models(tmp_path, monkeypatch):
    executable = Path(sys.executable).with_name("anaxigraph")
    if not executable.exists():
        pytest.skip("Installed CLI not available in this Python environment")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setenv("PATH", os.defpath)  # codex/claude absent: provider preflight fails locally.
    config = SimpleNamespace(
        runtime=executable, workflow_config=None, review_steps=set(), review_kinds=("patterns",)
    )
    first = runner.refresh_anaxigraph(config, repo, 0, tmp_path / "preparation")
    assert not first["qualified"] and first["mapping"]["status"] == "failed"
    run_id = first["mapping"]["run_id"]
    second = runner.refresh_anaxigraph(config, repo, 0, tmp_path / "preparation")
    assert not second["qualified"] and second["mapping"]["run_id"] == run_id
    receipts = [
        json.loads(path.read_text()) for path in (tmp_path / "preparation").glob("attempt-*.json")
    ]
    assert sum(x.get("command") == ["resume", run_id] for x in receipts) == 1
    assert all(x.get("finished") for x in receipts)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_invalid_reference_deadline_is_rejected_before_work(monkeypatch, value):
    monkeypatch.setattr(sys, "argv", ["runner", "--describe-track", f"--reference-timeout={value}"])
    with pytest.raises(SystemExit) as failure:
        runner.main()
    assert failure.value.code == 2


def test_reference_deadline_forwarded_and_testcase_timings_survive(tmp_path, monkeypatch):
    metrics = runner.metrics
    monkeypatch.setattr(metrics, "REFERENCE_TIMEOUT_SECONDS", 3600)

    def measured(argv, repo, timeout, env):
        assert timeout == 3600
        path = Path(next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--junitxml=")))
        path.write_text(
            '<testsuite><testcase classname="test_x" name="fast" time="0.1"/>'
            '<testcase classname="test_x" name="slow" time="2.0"/></testsuite>'
        )
        return SimpleNamespace(returncode=1, args=argv, stdout="", stderr="fixture")

    monkeypatch.setattr(metrics, "run", measured)
    _, info = metrics.coverage_by_test(tmp_path)
    assert info["testcase_duration_count"] == 2
    assert info["testcase_duration_seconds_sum"] == pytest.approx(2.1)
    assert info["slowest_testcases"][0] == {"id": "test_x::slow", "seconds": 2.0}
