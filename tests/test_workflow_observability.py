"""Failed attempts remain visible across retries without counting transport as acceptance."""

from __future__ import annotations

import json
import threading

from test_workflows import answer, app

from anaxigraph.evidence import Evidence
from anaxigraph.workflows.engine import _RUNNING, WorkflowRunner


def test_validation_rejection_is_retained_while_retry_runs_and_after_success(tmp_path):
    application = app(tmp_path, count=1)
    retry_started, release = threading.Event(), threading.Event()
    calls = 0

    def provider(request, *_args, **_kwargs):
        nonlocal calls
        if request["kind"] == "fresh-eyes":
            calls += 1
            if calls == 1:
                return {"producer": "synthetic-incomplete-result"}
            if calls == 2:
                retry_started.set()
                assert release.wait(5)
        return answer(request)

    runner = WorkflowRunner(application, provider)
    first = runner.start("fresh-eyes", background=False)
    assert first["status"] == "failed"
    assert first["failed_attempts"] == 1
    assert first["attempt_failures"][0]["phase"] == "validation"
    assert "three alternatives" in first["attempt_failures"][0]["error"]
    events = application.evidence.events(100)["items"]
    transport = [row for row in events if row["kind"] == "model_call"]
    assert all(row["payload"]["status"] == "completed" for row in transport)
    assert all(row["payload"]["phase"] == "provider" for row in transport)
    outcomes = [row["payload"] for row in events if row["kind"] == "analysis_result"]
    assert [row["status"] for row in outcomes] == ["accepted", "rejected"]
    assert outcomes[-1]["stage"] == "fresh-eyes"
    assert outcomes[-1]["attempt"] == 1
    assert outcomes[-1]["run_id"] == first["run_id"]
    assert application.reviews("fresh-eyes")["total"] == 0

    runner.resume(first["run_id"])
    try:
        assert retry_started.wait(5)
        active = runner.status(first["run_id"])
        assert active["status"] == "running"
        assert active["failed_batches"] == 0
        assert active["batch_retries"] == 1
        assert active["failed_attempts"] == 1
        assert active["attempt_history_complete"] is True
        assert active["attempt_failures"] == first["attempt_failures"]
        thread = _RUNNING[(str(application.root), first["run_id"])]
    finally:
        release.set()
    thread.join(5)
    assert not thread.is_alive()
    completed = WorkflowRunner(application, provider).status(first["run_id"])
    assert completed["status"] == "completed", completed
    assert completed["failed_batches"] == 0
    assert completed["batch_retries"] == 1
    assert completed["failed_attempts"] == 1
    assert completed["attempt_failures"] == first["attempt_failures"]
    persisted = json.loads(runner._path(first["run_id"]).read_text())
    assert persisted["batches"][1]["attempts"] == 2
    assert persisted["batches"][1]["attempt_failures"][0]["phase"] == "validation"
    results = [
        row["payload"]["status"]
        for row in Evidence(application.root).events(100)["items"]
        if row["kind"] == "analysis_result"
    ]
    assert results == ["accepted", "rejected", "accepted", "accepted"]


def test_attempt_history_is_bounded_but_counts_survive_resume_and_reload(tmp_path):
    application = app(tmp_path, count=1)

    def provider(request, *_args, **_kwargs):
        return (
            {"producer": "synthetic-incomplete-result"}
            if request["kind"] == "patterns"
            else answer(request)
        )

    runner = WorkflowRunner(application, provider)
    result = runner.start(
        "patterns",
        {
            "pattern_mode": "exhaustive",
            "stages": {"patterns": {"provider": "codex", "model": "fixture"}},
        },
        background=False,
    )
    for _ in range(4):
        result = runner.resume(result["run_id"], background=False)
    loaded = WorkflowRunner(application, provider).status(result["run_id"])
    assert loaded["status"] == "failed"
    assert loaded["batch_retries"] == 4
    assert loaded["failed_attempts"] == 5
    assert len(loaded["attempt_failures"]) == 3
    assert loaded["attempt_failures_truncated"] is True
    assert {row["attempt"] for row in loaded["attempt_failures"]} == {3, 4, 5}
    assert all(len(row["error"]) <= 200 for row in loaded["attempt_failures"])


def test_old_run_retries_are_reported_without_inventing_lost_failure_details(tmp_path):
    runner = WorkflowRunner(
        app(tmp_path, count=1), lambda request, *_args, **_kwargs: answer(request)
    )
    result = runner.start("mapping", background=False)
    path = runner._path(result["run_id"])
    legacy = json.loads(path.read_text())
    legacy.pop("attempt_history_version")
    legacy["batches"][0]["attempts"] = 3
    path.write_text(json.dumps(legacy))
    status = runner.status(result["run_id"])
    assert status["status"] == "completed"
    assert status["batch_attempts"] == 3
    assert status["batch_retries"] == 2
    assert status["failed_attempts"] == 0
    assert status["attempt_failures"] == []
    assert status["attempt_history_complete"] is False


def test_provider_failure_is_distinct_and_does_not_publish_raw_provider_output(tmp_path):
    application = app(tmp_path, count=1)

    def provider(request, worker, **_kwargs):
        if worker["model"] != "strong":
            raise ValueError("PRIVATE_RAW_PROVIDER_OUTPUT_AND_STDERR")
        return answer(request)

    config = {
        "stages": {
            "annotate": {
                "provider": "codex",
                "model": "weak",
                "fallbacks": [
                    {"provider": "codex", "model": model}
                    for model in ["weak2", "weak3", "weak4", "strong"]
                ],
            }
        }
    }
    result = WorkflowRunner(application, provider).start("mapping", config, background=False)
    assert result["status"] == "completed"
    assert result["failed_attempts"] == 4
    assert result["batch_attempts"] == 1
    assert result["batch_retries"] == 0
    assert len(result["attempt_failures"]) == 3
    assert result["attempt_failures_truncated"] is True
    assert result["attempt_failures"][0]["phase"] == "provider"
    assert "PRIVATE_RAW" not in json.dumps(result)
    events = application.evidence.events(100)["items"]
    assert "PRIVATE_RAW" not in json.dumps(events)
    assert [row["payload"]["status"] for row in events if row["kind"] == "model_call"] == [
        "failed",
        "failed",
        "failed",
        "failed",
        "completed",
    ]
    assert [row["payload"]["status"] for row in events if row["kind"] == "analysis_result"] == [
        "accepted"
    ]


def test_validation_message_is_sanitized_and_ledger_failure_never_retries_paid_work(
    tmp_path, monkeypatch
):
    application = app(tmp_path, count=1)

    def reject(*_args, **_kwargs):
        raise ValueError("Coverage invalid api_key=PRIVATE_SECRET\nPRIVATE_RAW_MODEL_OUTPUT")

    monkeypatch.setattr(application, "submit_analysis", reject)
    runner = WorkflowRunner(application, lambda request, *_args, **_kwargs: answer(request))
    rejected = runner.start(
        "mapping",
        {"stages": {"annotate": {"provider": "codex", "model": "fixture"}}},
        background=False,
    )
    assert rejected["failed_attempts"] == 1
    assert "[redacted]" in rejected["attempt_failures"][0]["error"]
    assert "PRIVATE_" not in json.dumps(application.evidence.events(100))
    assert "PRIVATE_" not in json.dumps(rejected)

    other = tmp_path / "other"
    other.mkdir()
    application = app(other, count=1)
    original = application.evidence.record
    calls = []

    def full(kind, *args, **kwargs):
        if kind == "analysis_result":
            raise ValueError("Synthetic exhausted evidence budget")
        return original(kind, *args, **kwargs)

    def provider(request, *_args, **_kwargs):
        calls.append(request["request_id"])
        return answer(request)

    monkeypatch.setattr(application.evidence, "record", full)
    result = WorkflowRunner(application, provider).start("mapping", background=False)
    assert result["status"] == "completed", result
    assert len(calls) == 1
    assert application.status()["annotations"]["current"] == 1
    assert result["evidence_error"] == "Synthetic exhausted evidence budget"
