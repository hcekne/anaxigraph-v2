"""Real durable workflow journeys with explicitly synthetic scout responses."""

from __future__ import annotations

import asyncio
import json
import threading
from collections import Counter

import pytest
from fastapi.testclient import TestClient
from test_architecture_scouts import answer as scout_answer
from test_workflows import answer, app

from anaxigraph.interfaces.cli import dispatch, parser
from anaxigraph.interfaces.http import create_app
from anaxigraph.interfaces.mcp import create_mcp
from anaxigraph.workflows.engine import WorkflowRunner


def finding(request):
    result = scout_answer(request)
    result["findings"][0]["evidence"] = [
        {"path": module["path"], "line": 1} for module in request["packet"]["modules"]
    ]
    return result


def test_component_workflow_runs_three_parallel_lenses_and_reuses_every_result(tmp_path):
    application = app(tmp_path, count=3)
    barrier = threading.Barrier(3)
    calls = Counter()

    def provider(request, worker, **_kwargs):
        calls[(request["kind"], request["packet"].get("lens"))] += 1
        if request["kind"] == "architecture":
            assert len(request["scope"]) == 3
            assert worker["model"] == "gpt-6-luna" and worker["reasoning_effort"] == "low"
            barrier.wait(timeout=5)
        return answer(request)

    runner = WorkflowRunner(application, provider)
    first = runner.start("architecture", {"workers": 3}, background=False)
    assert first["status"] == "completed", first
    assert first["reconciliation"]["assessed_pairs"] == 9
    assert first["reconciliation"]["outcomes"] == {"keep": 9}
    assert {lens for (kind, lens) in calls if kind == "architecture"} == {
        "boundaries",
        "packages",
        "verification",
    }
    for row in application.store.reviews(str(application.root), "architecture"):
        execution = row["result"]["result"]["execution"]
        assert execution["review_passes"] == []
        assert execution["pipeline"]["review_passes"][0]["model"] == "gpt-6.1-sol"
    previous = calls.copy()
    repeated = runner.start("architecture", {"workers": 3}, background=False)
    assert repeated["status"] == "completed", repeated
    assert repeated["reused_batches"] == repeated["total_batches"] == 3
    assert calls == previous


@pytest.mark.parametrize("review_model", [None, ""])
def test_positive_scout_is_strongly_corrected_and_reused_without_new_calls(
    tmp_path, monkeypatch, review_model
):
    application = app(tmp_path, count=2)
    calls = []
    expected = "gpt-6.1-sol" if review_model is None else "configured-review-model"
    monkeypatch.setattr("anaxigraph.workflows.engine.configured_model", lambda _provider: expected)

    def provider(request, worker, *, draft=None, **_kwargs):
        calls.append(
            (request["kind"], request["packet"].get("lens"), worker["model"], draft is not None)
        )
        if request["kind"] == "architecture" and request["packet"]["lens"] == "packages":
            if draft is None:
                return finding(request)
            assert worker["model"] == expected
            assert request["packet"]["finding_review_policy"]
            assert draft["findings"]
        return answer(request)

    config = (
        None
        if review_model is None
        else {"stages": {"architecture-review": {"provider": "codex", "model": review_model}}}
    )
    runner = WorkflowRunner(application, provider)
    first = runner.start("architecture", config, background=False)
    assert first["status"] == "completed", first
    assert [
        model for kind, lens, model, _ in calls if kind == "architecture" and lens == "packages"
    ] == ["gpt-6-luna", expected]
    assert sum(review for *_, review in calls) == 1
    row = next(
        row
        for row in application.store.reviews(str(application.root), "architecture")
        if row["result"]["request_options"]["lens"] == "packages"
    )
    result = row["result"]["result"]
    assert result["findings"] == []
    assert {item["outcome"] for item in result["assessments"]} == {"keep"}
    assert [worker["model"] for worker in result["execution"]["review_passes"]] == [expected]
    timing = next(batch for batch in first["batch_timings"] if batch.get("lens") == "packages")
    assert timing["model_calls"] == 2
    before = list(calls)
    repeated = runner.start("architecture", config, background=False)
    assert repeated["status"] == "completed", repeated
    assert repeated["reused_batches"] == 3
    assert calls == before


def test_explicit_scout_review_passes_run_in_order_only_for_initial_findings(tmp_path):
    application = app(tmp_path, count=1)
    calls = []

    def provider(request, worker, *, draft=None, **_kwargs):
        if request["kind"] == "architecture":
            lens = request["packet"]["lens"]
            calls.append((lens, worker["model"]))
            if lens == "packages":
                if draft is None:
                    return finding(request)
                if worker["model"] == "chosen-openai":
                    return draft
        return answer(request)

    config = {
        "review_passes": [
            {"provider": "codex", "model": "chosen-openai", "reasoning_effort": "medium"},
            {"provider": "claude", "model": "chosen-claude"},
        ]
    }
    result = WorkflowRunner(application, provider).start("architecture", config, background=False)
    assert result["status"] == "completed", result
    assert [model for lens, model in calls if lens == "packages"] == [
        "gpt-6-luna",
        "chosen-openai",
        "chosen-claude",
    ]
    assert [model for lens, model in calls if lens == "boundaries"] == ["gpt-6-luna"]
    assert [model for lens, model in calls if lens == "verification"] == ["gpt-6-luna"]
    for row in application.store.reviews(str(application.root), "architecture"):
        execution = row["result"]["result"]["execution"]
        assert execution["pipeline"]["review_passes"] == config["review_passes"]
        expected = (
            config["review_passes"]
            if row["result"]["request_options"]["lens"] == "packages"
            else []
        )
        assert execution["review_passes"] == expected


def test_finding_review_is_a_configurable_internal_stage_not_a_workflow(tmp_path):
    runner = WorkflowRunner(app(tmp_path, count=1))
    selected = runner.configuration(
        {"stages": {"architecture-review": {"provider": "claude", "model": "selected-reviewer"}}}
    )
    assert selected["stages"]["architecture-review"]["model"] == "selected-reviewer"
    with pytest.raises(ValueError, match="Workflow must be"):
        runner.start("architecture-review", background=False)
    with pytest.raises(ValueError, match="uses one worker"):
        runner.configuration(
            {
                "stages": {
                    "architecture-review": {
                        "provider": "codex",
                        "model": "selected",
                        "fallbacks": [{"provider": "codex", "model": "other"}],
                    }
                }
            }
        )


def test_legacy_scout_run_without_finding_review_keeps_its_recorded_pipeline(tmp_path):
    application = app(tmp_path, count=1)
    runner = WorkflowRunner(application, lambda request, *_args, **_kwargs: answer(request))
    first = runner.start("architecture", background=False)
    assert first["status"] == "completed", first
    path = runner._path(first["run_id"])
    legacy = json.loads(path.read_text())
    legacy["config"]["stages"].pop("architecture-review")
    legacy["status"] = "failed"
    for batch in legacy["batches"]:
        if batch.get("lens") == "packages":
            batch["status"] = "pending"
    path.write_text(json.dumps(legacy))
    calls = []

    def provider(request, worker, *, draft=None, **_kwargs):
        calls.append(worker["model"])
        assert request["kind"] == "architecture" and request["packet"]["lens"] == "packages"
        assert draft is None
        return finding(request)

    result = WorkflowRunner(application, provider).resume(first["run_id"], background=False)
    assert result["status"] == "completed", result
    assert calls == ["gpt-6-luna"]
    row = next(
        row
        for row in application.store.reviews(str(application.root), "architecture")
        if row["result"]["request_options"]["lens"] == "packages"
    )
    execution = row["result"]["result"]["execution"]
    assert execution["pipeline"]["review_passes"] == execution["review_passes"] == []


def test_pattern_screens_and_architecture_scouts_share_the_worker_pool(tmp_path):
    application = app(tmp_path, count=2)
    scout_started, screen_started = threading.Event(), threading.Event()

    def provider(request, *_args, **_kwargs):
        if request["kind"] == "architecture" and request["packet"]["lens"] == "boundaries":
            scout_started.set()
            assert screen_started.wait(5), "Screening was placed behind a serial scout barrier"
        if request["kind"] == "pattern-screen":
            screen_started.set()
            assert scout_started.wait(5)
        return answer(request)

    result = WorkflowRunner(application, provider).start(
        "patterns", {"workers": 2}, background=False
    )
    assert result["status"] == "completed", result
    assert result["reconciliation"]["architecture"]["assessed_pairs"] == 6


def test_failed_scout_retries_only_its_lens_and_cannot_hide_missing_coverage(tmp_path):
    application = app(tmp_path, count=2)
    failed = True
    calls = Counter()

    def provider(request, *_args, **_kwargs):
        lens = request["packet"].get("lens")
        calls[lens] += 1
        if failed and lens == "packages":
            raise ValueError("Synthetic package-scout interruption")
        return answer(request)

    runner = WorkflowRunner(application, provider)
    first = runner.start("architecture", background=False)
    assert first["status"] == "failed"
    assert application.reviews("architecture")["total"] == 2
    failed = False
    second = runner.resume(first["run_id"], background=False)
    assert second["status"] == "completed", second
    assert calls["boundaries"] == calls["verification"] == 1
    assert calls["packages"] == 3  # cheap attempt, fallback, then the requested retry
    path = runner._path(first["run_id"])
    saved = json.loads(path.read_text())
    saved["batches"] = [b for b in saved["batches"] if b.get("lens") != "packages"]
    saved["status"] = "failed"
    path.write_text(json.dumps(saved))
    missing = runner.resume(first["run_id"], background=False)
    assert missing["status"] == "failed"
    assert "module/lens" in missing["error"]


class Recording:
    def __getattr__(self, name):
        def call(*args, **kwargs):
            return {"operation": name, "args": list(args), "options": kwargs}

        return call


def test_cli_http_and_mcp_forward_architecture_lens_and_explicit_plan_decisions(tmp_path):
    application = Recording()
    decisions = [
        {"proposal_id": "review:0", "decision": "defer", "reason": "Schedule with its consumer."}
    ]
    saved = tmp_path / "decisions.json"
    saved.write_text(json.dumps(decisions))
    expected = {
        "operation": "analysis_request",
        "args": ["architecture", ["owner.py"]],
        "options": {"lens": "packages"},
    }
    assert (
        dispatch(
            application,
            parser().parse_args(
                ["request", "architecture", "--scope", "owner.py", "--lens", "packages"]
            ),
        )
        == expected
    )
    assessed = dispatch(
        application,
        parser().parse_args(
            ["assess", "Improve", "--files", "owner.py", "--decisions", str(saved)]
        ),
    )
    assert assessed["args"] == ["Improve", ["owner.py"], decisions]
    client = TestClient(
        create_app(application), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    )
    assert (
        client.post(
            "/api/analysis/request",
            json={"kind": "architecture", "scope": ["owner.py"], "lens": "packages"},
        ).json()
        == expected
    )
    assert (
        client.post(
            "/api/assess", json={"task": "Improve", "files": ["owner.py"], "decisions": decisions}
        ).json()["args"]
        == assessed["args"]
    )

    async def tools():
        server = create_mcp(application)
        requested = await server.call_tool(
            "analysis_request", {"kind": "architecture", "scope": ["owner.py"], "lens": "packages"}
        )
        assessed_mcp = await server.call_tool(
            "assess_plan", {"task": "Improve", "files": ["owner.py"], "decisions": decisions}
        )
        assert json.loads(requested[0].text) == expected
        assert json.loads(assessed_mcp[0].text)["args"] == assessed["args"]

    asyncio.run(tools())
