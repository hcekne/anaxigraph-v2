"""Real durable workflow journeys with explicitly synthetic scout responses."""

from __future__ import annotations

import asyncio
import json
import threading
from collections import Counter

from fastapi.testclient import TestClient
from test_workflows import answer, app

from anaxigraph.interfaces.cli import dispatch, parser
from anaxigraph.interfaces.http import create_app
from anaxigraph.interfaces.mcp import create_mcp
from anaxigraph.workflows.engine import WorkflowRunner


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
    previous = calls.copy()
    repeated = runner.start("architecture", {"workers": 3}, background=False)
    assert repeated["status"] == "completed", repeated
    assert repeated["reused_batches"] == repeated["total_batches"] == 3
    assert calls == previous


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
    client = TestClient(create_app(application))
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
