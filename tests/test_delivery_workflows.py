"""CI/CD reviews reuse the resumable engine without requiring semantic mapping."""

from __future__ import annotations

import asyncio
import json
import threading
from collections import Counter

from fastapi.testclient import TestClient
from test_workflows import answer, app

from anaxigraph.application import Application
from anaxigraph.interfaces.cli import dispatch, parser
from anaxigraph.interfaces.http import create_app
from anaxigraph.interfaces.mcp import create_mcp
from anaxigraph.workflows.engine import WorkflowRunner


def test_root_ci_review_needs_no_python_or_annotation_and_reuses_saved_result(tmp_path):
    (tmp_path / "client.js").write_text("export const greeting = 'hello';\n")
    application = Application(tmp_path)
    calls = []

    def provider(request, worker, **_kwargs):
        calls.append(request["kind"])
        assert request["kind"] == "delivery" and request["scope"] == []
        assert worker["model"] == "gpt-6-luna" and worker["reasoning_effort"] == "low"
        return answer(request)

    runner = WorkflowRunner(application, provider)
    first = runner.start("ci", background=False)
    assert first["status"] == "completed", first
    assert first["total_batches"] == first["completed_batches"] == 1
    assert application.status()["annotations"]["current"] == 0
    assert calls == ["delivery"]
    repeated = runner.start("delivery", background=False)
    assert repeated["status"] == "completed", repeated
    assert repeated["reused_batches"] == 1
    assert calls == ["delivery"]


def positive_review(request):
    result = answer(request)
    identities = request["packet"]["pipeline_ids"]
    result["assessments"] = [
        {"pipeline_id": identity, "outcome": "finding"} for identity in identities
    ]
    result["recommendations"] = [
        {
            "pipeline_ids": identities,
            "priority": 2,
            "title": "Split component checks from broader delivery checks",
            "bottleneck": "All checks share one workflow job without component input declarations.",
            "fast_path": ["Declare owned component inputs and run their local checks."],
            "broaden_when": ["Widen checks when public contracts or shared build inputs change."],
            "cache_strategy": ["Fingerprint source, runtime and lockfiles before result reuse."],
            "parallelism": ["Run independent component jobs concurrently."],
            "deployment_checks": ["Retain deployment compatibility and rollback checks."],
            "retain_checks": ["Keep integration and release checks on their declared cadence."],
            "next_steps": ["Measure job duration before implementing the proposed split."],
            "evidence": [{"path": identity, "line": 1} for identity in identities],
            "uncertainty": ["No project checks were executed by this synthetic review."],
        }
    ]
    return result


def test_positive_ci_review_uses_stronger_correction_and_reuses_final_result(tmp_path):
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "ci.yml").write_text(
        "name: CI\non: push\njobs:\n  tests:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: npm test\n"
    )
    application = Application(tmp_path)
    calls = []

    def provider(request, worker, *, draft=None, **_kwargs):
        calls.append((worker["model"], worker["reasoning_effort"], draft is not None))
        assert request["packet"]["finding_review_policy"]
        if draft is None:
            return positive_review(request)
        assert draft["recommendations"]
        return answer(request)

    runner = WorkflowRunner(application, provider)
    result = runner.start("ci", background=False)
    assert result["status"] == "completed", result
    assert calls == [("gpt-6-luna", "low", False), ("gpt-6.1-sol", "medium", True)]
    row = application.store.reviews(str(application.root), "delivery")[0]
    final = row["result"]["result"]
    assert final["recommendations"] == []
    assert final["execution"]["review_passes"] == [
        {"provider": "codex", "model": "gpt-6.1-sol", "reasoning_effort": "medium"}
    ]
    repeated = runner.start("ci", background=False)
    assert repeated["status"] == "completed" and repeated["reused_batches"] == 1
    assert len(calls) == 2


def test_ci_scope_filters_pipeline_evidence_while_pattern_module_scope_stays_separate(tmp_path):
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    paths = [f".github/workflows/{name}.yml" for name in ("checks", "release")]
    for path in paths:
        (tmp_path / path).write_text("jobs:\n  checks:\n    steps:\n      - run: npm test\n")
    application = app(tmp_path, count=1)
    scopes = []

    def provider(request, *_args, **_kwargs):
        if request["kind"] == "delivery":
            assert request["scope"] == []
            scopes.append(request["packet"]["pipeline_ids"])
        return answer(request)

    runner = WorkflowRunner(application, provider)
    scoped = runner.start("ci", scope=[paths[0]], background=False)
    assert scoped["status"] == "completed", scoped
    assert scopes == [[paths[0]]]
    patterns = runner.start("patterns", scope=["unit0.py"], background=False)
    assert patterns["status"] == "completed", patterns
    assert scopes == [[paths[0]], paths]


def test_explicit_ci_review_passes_replace_default_and_remain_ordered(tmp_path):
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "ci.yml").write_text("jobs:\n  checks:\n    steps:\n      - run: npm test\n")
    calls = []

    def provider(request, worker, *, draft=None, **_kwargs):
        calls.append(worker["model"])
        if draft is None:
            return positive_review(request)
        return draft if worker["model"] == "first-review" else answer(request)

    result = WorkflowRunner(Application(tmp_path), provider).start(
        "delivery",
        {
            "review_passes": [
                {"provider": "codex", "model": "first-review"},
                {"provider": "claude", "model": "second-review"},
            ]
        },
        background=False,
    )
    assert result["status"] == "completed", result
    assert calls == ["gpt-6-luna", "first-review", "second-review"]


def test_pattern_ci_review_shares_pool_with_screens_and_keeps_three_architecture_lenses(tmp_path):
    application = app(tmp_path, count=2)
    delivery_started, screen_started = threading.Event(), threading.Event()
    calls = Counter()

    def provider(request, *_args, **_kwargs):
        calls[(request["kind"], request["packet"].get("lens"))] += 1
        if request["kind"] == "delivery":
            assert request["scope"] == []
            delivery_started.set()
            assert screen_started.wait(5), "CI review must not block independent screening"
        if request["kind"] == "pattern-screen":
            screen_started.set()
            assert delivery_started.wait(5)
        return answer(request)

    result = WorkflowRunner(application, provider).start(
        "patterns", {"workers": 2}, background=False
    )
    assert result["status"] == "completed", result
    assert calls[("delivery", None)] == 1
    assert {lens for (kind, lens) in calls if kind == "architecture"} == {
        "boundaries",
        "packages",
        "verification",
    }
    assert result["reconciliation"]["delivery"]
    assert result["reconciliation"]["architecture"]["assessed_pairs"] == 6


def test_failed_ci_batch_resumes_alone_and_missing_ci_cannot_reconcile(tmp_path):
    application = app(tmp_path, count=1)
    failing = True
    calls = Counter()

    def provider(request, *_args, **_kwargs):
        calls[request["kind"]] += 1
        if failing and request["kind"] == "delivery":
            raise ValueError("Synthetic CI worker interruption")
        return answer(request)

    runner = WorkflowRunner(application, provider)
    first = runner.start("patterns", background=False)
    assert first["status"] == "failed", first
    assert application.reviews("architecture")["total"] == 3
    assert calls["delivery"] == 2  # Luna and its configured stronger fallback
    completed_counts = calls.copy()
    failing = False
    completed = runner.resume(first["run_id"], background=False)
    assert completed["status"] == "completed", completed
    assert calls["delivery"] == 3
    for kind in ("annotate", "architecture", "pattern-screen"):
        assert calls[kind] == completed_counts[kind]
    path = runner._path(first["run_id"])
    saved = json.loads(path.read_text())
    saved["batches"] = [batch for batch in saved["batches"] if batch["kind"] != "delivery"]
    saved["status"] = "failed"
    path.write_text(json.dumps(saved))
    missing = runner.resume(first["run_id"], background=False)
    assert missing["status"] == "failed", missing
    assert "exactly one accepted root review" in missing["error"]


def test_markerless_pattern_resume_keeps_its_recorded_protocol(tmp_path):
    application = app(tmp_path, count=1)
    runner = WorkflowRunner(application, lambda request, *_args, **_kwargs: answer(request))
    first = runner.start("patterns", background=False)
    assert first["status"] == "completed", first
    path = runner._path(first["run_id"])
    saved = json.loads(path.read_text())
    saved.pop("delivery_review_version")
    saved["config"]["stages"].pop("delivery")
    saved["config"]["stages"].pop("delivery-review")
    saved["batches"] = [batch for batch in saved["batches"] if batch["kind"] != "delivery"]
    saved["stages_planned"].remove("delivery")
    saved["status"] = "failed"
    path.write_text(json.dumps(saved))

    def no_new_calls(*_args, **_kwargs):
        raise AssertionError("A legacy resume must not introduce a new CI job")

    resumed = WorkflowRunner(application, no_new_calls).resume(first["run_id"], background=False)
    assert resumed["status"] == "completed", resumed
    assert "delivery" not in resumed["reconciliation"]


class Recording:
    def __getattr__(self, name):
        def call(*args, **kwargs):
            return {"operation": name, "args": list(args), "options": kwargs}

        return call


def test_ci_plan_transports_preserve_declared_changes_and_runtime_inputs(tmp_path):
    paths, runtime = ["payments/charge.py"], {"platform": "linux", "python": "3.12"}
    path_file, context_file = tmp_path / "paths.json", tmp_path / "context.json"
    path_file.write_text(json.dumps(paths))
    context_file.write_text(json.dumps(runtime))
    expected = {
        "operation": "ci_plan",
        "args": [],
        "options": {
            "base_ref": None,
            "base_snapshot_id": None,
            "changed_paths": paths,
            "cadence": "release",
            "runtime_context": runtime,
        },
    }
    application = Recording()
    result = dispatch(
        application,
        parser().parse_args(
            [
                "ci-plan",
                "--changed-paths",
                str(path_file),
                "--cadence",
                "release",
                "--runtime-context",
                str(context_file),
            ]
        ),
    )
    assert result == expected
    client = TestClient(
        create_app(application), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    )
    assert client.post("/api/ci/plan", json=expected["options"]).json() == expected
    assert client.post("/api/ci/plan", json={"cadence": "never"}).status_code == 422
    assert "Review CI/CD" in client.get("/").text
    assert "Preview affected checks" in client.get("/").text
    assert "When to widen checks" in client.get("/assets/ci.js").text

    async def exercise():
        server = create_mcp(application)
        result = await server.call_tool("ci_plan", expected["options"])
        assert json.loads(result[0].text) == expected
        delivery = await server.call_tool("analysis_request", {"kind": "delivery"})
        assert json.loads(delivery[0].text)["args"] == ["delivery", None]
        started = await server.call_tool("workflow_start", {"kind": "ci"})
        assert json.loads(started[0].text)["args"] == ["ci", None, None]

    asyncio.run(exercise())
