"""Screen cheaply, review selected designs, and retain measured work across retries."""

from __future__ import annotations

import json
import threading
import time

import pytest
from test_workflows import answer, app

from anaxigraph.application import Application
from anaxigraph.workflows.engine import _RUNNING, WorkflowRunner


def test_defaults_are_fast_and_existing_saved_model_choices_are_preserved(tmp_path):
    runner = WorkflowRunner(app(tmp_path, count=1))
    defaults = runner.configuration()
    assert defaults["workers"] == 32
    assert defaults["pattern_mode"] == "screened"
    for kind in ("annotate", "pattern-screen", "architecture"):
        assert defaults["stages"][kind]["model"] == "gpt-6-luna"
        assert defaults["stages"][kind]["reasoning_effort"] == "low"
    detail = defaults["stages"]["patterns"]
    assert detail["model"] == "gpt-6.1-sol" and detail["reasoning_effort"] == "medium"
    assert detail["fallbacks"][0]["reasoning_effort"] == "xhigh"
    old = {
        "workers": 7,
        "stages": {
            "annotate": {"provider": "claude", "model": "selected"},
            "patterns": {
                "provider": "codex",
                "model": "selected-detail",
                "reasoning_effort": "xhigh",
            },
        },
    }
    (runner.directory / "settings.json").write_text(json.dumps(old))
    retained = runner.configuration()
    assert retained["workers"] == 7
    assert retained["stages"]["annotate"]["provider"] == "claude"
    assert retained["stages"]["annotate"]["model"] == "selected"
    assert retained["stages"]["patterns"]["model"] == "selected-detail"
    assert retained["stages"]["patterns"]["reasoning_effort"] == "xhigh"
    assert retained["stages"]["pattern-screen"]["model"] == "gpt-6-luna"


def test_screened_workflow_shortlists_skips_empty_and_records_models_tokens_and_timing(tmp_path):
    application = app(tmp_path, count=3)
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_extra.py").write_text("def test_operation():\n    return 1\n")
    (tmp_path / "__init__.py").write_text('"""Package marker."""\n')
    calls = []

    def provider(request, worker, **_kwargs):
        calls.append((request["kind"], request["packet"]["modules"][0]["path"], dict(worker)))
        time.sleep(0.002)
        result = answer(request)
        if request["kind"] == "pattern-screen":
            selected = result["selections"][0]
            if selected["module_id"].endswith("unit1.py"):
                selected["pattern_ids"] = []
            elif selected["module_id"].endswith("unit2.py"):
                selected["pattern_ids"] = selected["pattern_ids"][:1]
        if (
            request["kind"] == "patterns"
            and request["scope"][0].endswith("unit0.py")
            and worker["reasoning_effort"] == "medium"
        ):
            result = {"producer": "synthetic-invalid-detail"}
        result["_anaxigraph_usage"] = {"output_tokens": 7}
        return result

    runner = WorkflowRunner(application, provider)
    result = runner.start("patterns", background=False)
    assert result["status"] == "completed", result
    reconciliation = result["reconciliation"]
    assert reconciliation["screened_pairs"] == 399
    assert reconciliation["selected_pairs"] == reconciliation["detailed_pairs"] == 3
    assert {kind for kind, *_ in calls} == {
        "annotate",
        "pattern-screen",
        "patterns",
        "architecture",
    }
    assert all(not path.startswith("tests/") and path != "__init__.py" for _, path, _ in calls)
    assert all(
        worker["model"] == "gpt-6-luna" and worker["reasoning_effort"] == "low"
        for kind, _, worker in calls
        if kind != "patterns"
    )
    assert not any(kind == "patterns" and path == "unit1.py" for kind, path, _ in calls)
    assert [
        worker["reasoning_effort"]
        for kind, path, worker in calls
        if kind == "patterns" and path == "unit0.py"
    ] == ["medium", "xhigh"]
    state = json.loads(runner._path(result["run_id"]).read_text())
    assert "selections" not in json.dumps(state)
    assert "pattern_ids" not in json.dumps(state)
    assert all(
        batch["finished_at"] >= batch["started_at"] and batch["elapsed_seconds"] > 0
        for batch in state["batches"]
    )
    skipped = next(batch for batch in state["batches"] if batch.get("skipped"))
    assert skipped["model_calls"] == skipped["model_seconds"] == 0
    assert sum(model["output_tokens"] for model in result["model_usage"]) == 7 * len(calls)
    assert all(model["elapsed_seconds"] > 0 for model in result["model_usage"])
    before = len(calls)
    repeated = runner.start("patterns", background=False)
    assert repeated["status"] == "completed", repeated
    assert repeated["reused_batches"] == 8
    assert len(calls) == before
    assert repeated["model_usage"] == []
    assert all(
        batch["model_calls"] == batch["model_seconds"] == 0 for batch in repeated["batch_timings"]
    )


def test_failed_screen_resumes_only_failed_batch_with_cumulative_attempt_time(tmp_path):
    application = app(tmp_path, count=2)
    failing = True
    calls = []

    def provider(request, worker, **_kwargs):
        module = request["packet"]["modules"][0]["id"]
        calls.append((request["kind"], module))
        time.sleep(0.002)
        if failing and request["kind"] == "pattern-screen" and module.endswith("unit1.py"):
            raise ValueError("Synthetic screen transport failure")
        return answer(request)

    runner = WorkflowRunner(application, provider)
    first = runner.start("patterns", background=False)
    assert first["status"] == "failed", first
    state = json.loads(runner._path(first["run_id"]).read_text())
    failed = next(batch for batch in state["batches"] if batch["status"] == "failed")
    elapsed = failed["elapsed_seconds"]
    assert elapsed > 0
    assert failed["attempt_history"][0]["status"] == "failed"
    assert not any(kind == "patterns" for kind, _ in calls)
    failing = False
    completed = runner.resume(first["run_id"], background=False)
    assert completed["status"] == "completed", completed
    state = json.loads(runner._path(first["run_id"]).read_text())
    retried = next(batch for batch in state["batches"] if batch["id"] == failed["id"])
    assert retried["attempts"] == 2 and retried["elapsed_seconds"] > elapsed
    assert [attempt["status"] for attempt in retried["attempt_history"]] == ["failed", "completed"]
    assert calls.count(("pattern-screen", "module:unit0.py")) == 1


def test_parallel_module_pool_respects_explicit_worker_bound(tmp_path):
    application = app(tmp_path, count=3)
    ready, release = threading.Event(), threading.Event()
    mutex = threading.Lock()
    active = maximum = 0

    def provider(request, *_args, **_kwargs):
        nonlocal active, maximum
        with mutex:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                ready.set()
        assert release.wait(5)
        with mutex:
            active -= 1
        return answer(request)

    runner = WorkflowRunner(application, provider)
    run = runner.start("mapping", {"workers": 2})
    try:
        assert ready.wait(5)
        assert runner.status(run["run_id"])["active_workers"] == 2
        thread = _RUNNING[(str(application.root), run["run_id"])]
    finally:
        release.set()
    thread.join(10)
    assert not thread.is_alive()
    assert runner.status(run["run_id"])["status"] == "completed"
    assert maximum == 2


def test_no_eligible_production_modules_finishes_without_paid_calls(tmp_path):
    (tmp_path / "__init__.py").write_text("pass\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_only.py").write_text("def test_case():\n    return 1\n")
    application = Application(tmp_path)

    def provider(*_args, **_kwargs):
        raise AssertionError("No worker should run for an empty default scope")

    result = WorkflowRunner(application, provider).start("patterns", background=False)
    assert result["status"] == "completed", result
    assert result["reconciliation"]["mode"] == "empty"
    assert result["total_batches"] == 0


def test_legacy_exhaustive_run_resumes_its_original_all_module_scope(tmp_path):
    application = app(tmp_path, count=1)
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_operation.py").write_text("def test_result():\n    return 1\n")
    failed = True

    def provider(request, *_args, **_kwargs):
        if failed and request["kind"] == "patterns":
            raise ValueError("Synthetic interruption before detail acceptance")
        assert request["kind"] != "pattern-screen"
        return answer(request)

    runner = WorkflowRunner(application, provider)
    first = runner.start(
        "patterns",
        {"pattern_mode": "exhaustive"},
        scope=["unit0.py", "tests/test_operation.py"],
        background=False,
    )
    assert first["status"] == "failed"
    path = runner._path(first["run_id"])
    legacy = json.loads(path.read_text())
    legacy["config"].pop("pattern_mode")
    legacy.pop("architecture_scouts_version")
    legacy["batches"] = [batch for batch in legacy["batches"] if batch["kind"] != "architecture"]
    legacy["stages_planned"].remove("architecture")
    legacy["scope"] = None
    path.write_text(json.dumps(legacy))
    failed = False
    completed = runner.resume(first["run_id"], background=False)
    assert completed["status"] == "completed", completed
    assert completed["reconciliation"]["evaluated_pairs"] == 266


def test_full_usage_ledger_does_not_hide_timing_or_retry_a_saved_analysis(tmp_path, monkeypatch):
    application = app(tmp_path, count=1)
    calls = []

    def provider(request, *_args, **_kwargs):
        calls.append(request["request_id"])
        return answer(request)

    def full(*_args, **_kwargs):
        raise ValueError("Synthetic evidence budget exhausted")

    monkeypatch.setattr(application.evidence, "model_call", full)
    result = WorkflowRunner(application, provider).start("mapping", background=False)
    assert result["status"] == "completed", result
    assert len(calls) == 1
    assert result["evidence_error"] == "Synthetic evidence budget exhausted"
    assert result["model_usage"][0]["calls"] == 1
    assert result["model_usage"][0]["output_tokens"] is None
    assert result["model_usage"][0]["unknown_output_calls"] == 1
    assert result["batch_timings"][0]["model_calls"] == 1


def test_batch_timing_pages_expose_every_saved_batch_without_more_model_calls(tmp_path):
    application = app(tmp_path, count=13)
    calls = []

    def provider(request, *_args, **_kwargs):
        calls.append(request["request_id"])
        return answer(request)

    runner = WorkflowRunner(application, provider)
    result = runner.start("mapping", {"workers": 3}, background=False)
    assert result["status"] == "completed", result
    assert len(calls) == 13
    assert [batch["id"] for batch in result["batch_timings"]] == list(range(1, 11))
    assert result["next_batch_offset"] == 10
    assert result["batch_timings_total"] == 13
    saved = runner._path(result["run_id"]).read_bytes()
    reloaded = WorkflowRunner(application, provider)
    second = reloaded.status(result["run_id"], batch_offset=10)
    assert [batch["id"] for batch in second["batch_timings"]] == [11, 12, 13]
    assert second["next_batch_offset"] is None
    assert reloaded.status(result["run_id"], batch_offset=13)["batch_timings"] == []
    index = reloaded.status()["items"][0]
    assert "batch_timings" not in index and "model_usage" not in index
    assert index["stage_timings"][0]["completed_batches"] == 13
    assert len(calls) == 13 and runner._path(result["run_id"]).read_bytes() == saved
    events = application.evidence.events(100)["items"]
    assert len([event for event in events if event["kind"] == "model_call"]) == 13


@pytest.mark.parametrize(
    "paging",
    [
        {"batch_offset": -1},
        {"batch_offset": 100_001},
        {"batch_offset": True},
        {"batch_offset": 1.0},
        {"batch_limit": 0},
        {"batch_limit": 51},
        {"batch_limit": False},
        {"batch_limit": 10.0},
    ],
)
def test_batch_timing_paging_rejects_invalid_bounds(tmp_path, paging):
    runner = WorkflowRunner(app(tmp_path, count=1))
    with pytest.raises(ValueError, match="batch_"):
        runner.status(**paging)
