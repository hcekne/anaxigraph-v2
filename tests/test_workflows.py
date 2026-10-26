"""Real persistence/retry journeys; fake model output is labelled as synthetic."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from collections import Counter

import pytest

from anaxigraph.application import Application
from anaxigraph.evidence import Evidence, provider_usage
from anaxigraph.workflows.engine import WorkflowRunner
from anaxigraph.workflows.providers import _result, _verified, execute


def answer(request):
    packet, kind = request["packet"], request["kind"]
    if kind == "annotate":
        return {
            "producer": "synthetic-workflow-fixture",
            "annotations": [
                {
                    "node_id": module["id"],
                    "overview": "Provides a small callable operation.",
                    "detail": "Returns an observable result to a caller.",
                    "capabilities": ["Provide an observable result to a caller."],
                    "functions": [
                        {"node_id": fn["id"], "overview": "Return an observable result."}
                        for fn in packet["functions"]
                        if fn["path"] == module["path"]
                    ],
                    "contracts": [],
                    "uncertainty": [],
                }
                for module in packet["modules"]
            ],
        }
    if kind == "patterns":
        return {
            "producer": "synthetic-workflow-fixture",
            "evaluations": [
                {
                    "module_id": module["id"],
                    "pattern_id": pattern["id"],
                    "opportunity": 0,
                    "current_fit": "absent",
                    "proposed_change": "No change.",
                    "pros": ["None."],
                    "cons": ["Unnecessary complexity."],
                    "alternative": "Keep the local function.",
                    "evidence": [],
                }
                for module in packet["modules"]
                for pattern in packet["catalogue"]
            ],
        }
    requirements = [item["id"] for item in packet["requirements"]]
    return {
        "producer": "synthetic-workflow-fixture",
        "functional_summary": "Deliver observable results to callers.",
        "functional_requirements": ["Deliver observable results to callers."],
        "functional_coverage": [requirements],
        "alternatives": [
            {
                "name": name,
                "design": f"Deliver the behavior using {name}.",
                "requirements": requirements,
                "pros": ["Clear responsibility."],
                "cons": ["Check operational costs."],
                "diagram": "flowchart TB\nUser --> Service",
            }
            for name in ("a local operation", "a message queue", "a declarative engine")
        ],
    }


def app(tmp_path, count=2):
    for index in range(count):
        (tmp_path / f"unit{index}.py").write_text(f"def operation_{index}():\n    return {index}\n")
    return Application(tmp_path)


def test_failed_batches_preserve_success_and_resume_only_failed(tmp_path):
    application = app(tmp_path)
    calls = Counter()
    failure = True

    def provider(request, _worker, **_kwargs):
        nonlocal failure
        module = request["packet"]["modules"][0]["id"]
        calls[module] += 1
        if failure and module.endswith("unit1.py"):
            raise ValueError("Synthetic interrupted worker")
        return answer(request)

    runner = WorkflowRunner(application, provider)
    first = runner.start("mapping", background=False)
    assert first["status"] == "failed"
    assert first["completed_batches"] == 1
    assert application.status()["annotations"]["current"] == 1
    assert (tmp_path / "anaxigraph" / "record.json").is_file()
    failure = False
    # A new runner proves that retry state comes from disk rather than the thread.
    resumed = WorkflowRunner(application, provider).resume(first["run_id"], background=False)
    assert resumed["status"] == "completed", resumed
    assert resumed["completed_batches"] == resumed["total_batches"] == 2
    assert sorted(calls.values()) == [1, 2]
    assert resumed["phase"] == "Final reconciliation"
    assert application.status()["annotations"]["current"] == 2


def test_parallel_batches_keep_provider_passes_ordered_and_use_stronger_fallback(tmp_path):
    application = app(tmp_path, count=3)
    calls = {}
    mutex = threading.Lock()
    barrier = threading.Barrier(3)

    def provider(request, worker, *, draft=None, **_kwargs):
        module = request["packet"]["modules"][0]["id"]
        with mutex:
            calls.setdefault(module, []).append(worker["model"])
        if worker["model"] == "weak":
            barrier.wait(timeout=5)
            return {"producer": "synthetic-invalid-draft"}
        if worker["model"] == "strong":
            assert draft is None
        if worker["model"] == "claude-review":
            assert draft["producer"] == "synthetic-workflow-fixture"
        return answer(request)

    config = {
        "workers": 3,
        "stages": {
            "annotate": {
                "provider": "codex",
                "model": "weak",
                "fallbacks": [{"provider": "codex", "model": "strong"}],
            }
        },
        "review_passes": [
            {"provider": "codex", "model": "openai-review"},
            {"provider": "claude", "model": "claude-review"},
        ],
    }
    # The first review validates/corrects the weak draft; stages stay ordered.
    result = WorkflowRunner(application, provider).start("mapping", config, background=False)
    assert result["status"] == "completed", result
    assert all(
        sequence == ["weak", "openai-review", "claude-review"] for sequence in calls.values()
    )


def test_validation_failure_tries_configured_stronger_model(tmp_path):
    application = app(tmp_path, count=1)
    models = []

    def provider(request, worker, **_kwargs):
        models.append(worker["model"])
        return {"producer": "synthetic-invalid"} if worker["model"] == "weak" else answer(request)

    config = {
        "stages": {
            "annotate": {
                "provider": "codex",
                "model": "weak",
                "fallbacks": [{"provider": "codex", "model": "strong"}],
            }
        }
    }
    result = WorkflowRunner(application, provider).start("mapping", config, background=False)
    assert result["status"] == "completed", result
    assert models == ["weak", "strong"]


def test_patterns_and_blind_hierarchical_design_are_saved_and_reused(tmp_path):
    application = app(tmp_path, count=2)
    calls = Counter()

    def provider(request, _worker, **_kwargs):
        calls[request["kind"]] += 1
        if request["kind"] == "fresh-eyes":
            serialized = json.dumps(request)
            assert "unit0.py" not in serialized
            assert "operation_0" not in serialized
        return answer(request)

    runner = WorkflowRunner(application, provider)
    patterns = runner.start("patterns", background=False)
    assert patterns["status"] == "completed", patterns
    assert patterns["reconciliation"]["evaluated_pairs"] == 266
    previous = calls.copy()
    repeated = runner.start("patterns", background=False)
    assert repeated["status"] == "completed", repeated
    assert repeated["reused_batches"] == 2
    assert calls == previous
    fresh = runner.start("fresh-eyes", background=False)
    assert fresh["status"] == "completed", fresh
    assert calls["fresh-eyes"] == 2  # subsystem alternatives, then product alternatives
    assert len(list((tmp_path / ".anaxigraph-v2" / "workflows").glob("*.json"))) <= 12


def test_dead_worker_in_live_service_reports_interrupted_and_can_resume(tmp_path):
    application = app(tmp_path, count=1)
    runner = WorkflowRunner(application, lambda request, *_args, **_kwargs: answer(request))
    result = runner.start("mapping", background=False)
    path = runner._path(result["run_id"])
    state = json.loads(path.read_text())
    state["status"], state["pid"] = "running", os.getpid()
    path.write_text(json.dumps(state))
    assert runner.status(result["run_id"])["status"] == "interrupted"
    assert runner.resume(result["run_id"], background=False)["status"] == "completed"


def test_new_review_pipeline_is_executed_instead_of_reusing_unreviewed_output(tmp_path):
    application = app(tmp_path, count=1)
    calls = []

    def provider(request, worker, **_kwargs):
        calls.append((request["kind"], worker["provider"]))
        return answer(request)

    runner = WorkflowRunner(application, provider)
    assert runner.start("patterns", background=False)["status"] == "completed"
    before = len(calls)
    reviewed = {"review_passes": [{"provider": "claude", "model": "fixture-reviewer"}]}
    changed = runner.start("patterns", reviewed, background=False)
    assert changed["status"] == "completed", changed
    assert len(calls) > before
    assert ("annotate", "claude") in calls and ("patterns", "claude") in calls
    completed = len(calls)
    assert runner.start("patterns", reviewed, background=False)["status"] == "completed"
    assert len(calls) == completed


def test_cli_provider_uses_real_subprocess_without_repository_or_tools(tmp_path, monkeypatch):
    executable = tmp_path / "codex"
    executable.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
if 'features' in sys.argv:
    flags = [sys.argv[i+1] for i,x in enumerate(sys.argv[:-1]) if x == '--disable']
    print('\\n'.join(x+' stable false' for x in flags))
else:
    assert not list(pathlib.Path.cwd().glob('*.py'))
    assert '--ignore-user-config' in sys.argv and '--ignore-rules' in sys.argv
    assert '--sandbox' in sys.argv and 'read-only' in sys.argv
    assert 'model_reasoning_effort="xhigh"' in sys.argv
    assert 'CODEX_THREAD_ID' not in os.environ
    payload = sys.stdin.read()
    assert 'BEHAVIOR ONLY' in payload and 'secret.py' not in payload
    target = pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])
    assert '--json' in sys.argv
    target.write_text(json.dumps({'producer':'synthetic-executable','alternatives':[], '_anaxigraph_usage':{'cost_usd':999}}))
    print(json.dumps({'type':'turn.completed','usage':{'input_tokens':101,'cached_input_tokens':80,'output_tokens':7}}))
""")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CODEX_THREAD_ID", "must-not-reach-worker")
    _verified.cache_clear()
    request = {
        "request_id": "r",
        "kind": "fresh-eyes",
        "packet": {"instruction": "BEHAVIOR ONLY"},
        "response_schema": {},
        "scope": ["secret.py"],
    }
    result = execute(
        request,
        {"provider": "codex", "model": "fixture", "reasoning_effort": "xhigh"},
        timeout=None,
    )
    assert result["producer"] == "synthetic-executable"
    assert result["_anaxigraph_usage"]["input_tokens"] == 101
    assert result["_anaxigraph_usage"]["output_tokens"] == 7
    assert result["_anaxigraph_usage"]["cost_usd"] is None
    _verified.cache_clear()


def test_worker_transport_parses_only_json_objects_and_claude_envelopes():
    assert _result('```json\n{"producer":"fixture"}\n```')["producer"] == "fixture"
    assert (
        _result('{"type":"result","result":"{\\"producer\\":\\"fixture\\"}"}')["producer"]
        == "fixture"
    )
    with pytest.raises(ValueError):
        _result("[]")


def test_claude_actual_subprocess_usage_is_retained_without_result_text(tmp_path, monkeypatch):
    executable = tmp_path / "claude"
    executable.write_text("""#!/usr/bin/env python3
import json, sys
if '--help' in sys.argv:
    print('--tools --restricted --strict-mcp-config --setting-sources --system-prompt --no-session-persistence --safe-mode')
else:
    assert '--tools' in sys.argv and '--safe-mode' in sys.argv
    print(json.dumps({'type':'result','result':'{"producer":"synthetic-claude"}', 'total_cost_usd':0.015, 'usage':{'input_tokens':20,'cache_read_input_tokens':80,'cache_creation_input_tokens':0,'output_tokens':5}}))
""")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    _verified.cache_clear()
    result = execute(
        {"kind": "probe", "packet": {}, "response_schema": {}},
        {"provider": "claude", "model": "fixture"},
    )
    assert result["producer"] == "synthetic-claude"
    assert result["_anaxigraph_usage"]["input_tokens"] == 100
    assert result["_anaxigraph_usage"]["cost_usd"] == 0.015
    assert result["_anaxigraph_usage"]["cost_source"] == "provider-reported-estimate"
    _verified.cache_clear()


def test_workflow_records_each_fallback_review_and_reuse_without_saving_usage_in_answers(tmp_path):
    application = app(tmp_path, count=1)
    application.evidence = Evidence(tmp_path, arm="treatment", experiment_id="fixture")
    task = application.evidence.start_task("Implement ranker", "start", "before", "baseline")

    def provider(request, worker, *, draft=None, **_kwargs):
        assert draft is None or "_anaxigraph_usage" not in draft
        if worker["model"] == "weak":
            raise ValueError("Synthetic transport failure")
        result = answer(request)
        transport = {"type": "turn.completed", "usage": {"input_tokens": 50, "output_tokens": 5}}
        if worker["provider"] == "claude":
            transport = {
                "type": "result",
                "total_cost_usd": 0.02,
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 0,
                    "output_tokens": 5,
                },
            }
        result["_anaxigraph_usage"] = provider_usage(worker["provider"], transport)
        return result

    config = {
        "stages": {
            "annotate": {
                "provider": "codex",
                "model": "weak",
                "fallbacks": [{"provider": "codex", "model": "strong"}],
            }
        },
        "review_passes": [{"provider": "claude", "model": "reviewer"}],
    }
    runner = WorkflowRunner(application, provider)
    run = runner.start("mapping", config, background=False)
    assert run["status"] == "completed", run
    events = [
        row for row in application.evidence.events(100)["items"] if row["kind"] == "model_call"
    ]
    assert [row["payload"]["status"] for row in events] == ["failed", "completed", "completed"]
    assert [row["payload"]["model"] for row in events] == ["weak", "strong", "reviewer"]
    assert all(row["task_id"] == task for row in events)
    assert events[0]["payload"]["cost_usd"] is None
    assert events[1]["payload"]["input_tokens"] == 50
    assert events[2]["payload"]["stage"] == "annotate:review"
    assert events[2]["payload"]["cost_usd"] == 0.02
    module = application.modules()["items"][0]["id"]
    assert "_anaxigraph_usage" not in json.dumps(application.inspect(module))
    repeated = runner.start("mapping", config, background=False)
    assert repeated["status"] == "completed" and repeated["reused_batches"] == 1
    assert application.evidence.events(100)["items"][-1]["payload"]["reused"] is True


def test_trial_ledger_failure_does_not_trigger_another_paid_model_attempt(tmp_path, monkeypatch):
    application = app(tmp_path, count=1)
    calls = []

    def full_ledger(*args, **kwargs):
        raise ValueError("Trial storage budget reached")

    # Keep the real recorder/path contract; fail only the exhausted write.
    monkeypatch.setattr(application.evidence, "model_call", full_ledger)

    def provider(request, _worker, **_kwargs):
        calls.append(request["request_id"])
        return answer(request)

    result = WorkflowRunner(application, provider).start("mapping", background=False)
    assert result["status"] == "completed", result
    assert len(calls) == 1 and result["evidence_error"] == "Trial storage budget reached"


def test_workflow_budget_failure_keeps_last_settings_and_removes_temporary_output(tmp_path):
    application = app(tmp_path, count=1)
    runner = WorkflowRunner(application)
    runner.configuration({"workers": 4})
    settings = runner.directory / "settings.json"
    before = settings.read_bytes()
    record = tmp_path / "anaxigraph"
    record.mkdir()
    # Sparse fixture: exercise the byte accounting without allocating 200 MB.
    with (record / "record.pre-v0.json").open("wb") as stream:
        stream.truncate(200_000_000)
    with pytest.raises(ValueError, match="200 MB"):
        runner.configuration({"workers": 8})
    assert settings.read_bytes() == before
    assert not list(runner.directory.glob("*.tmp"))


def test_oversized_real_subprocess_output_is_rejected_and_temp_files_are_cleaned(
    tmp_path, monkeypatch
):
    executable = tmp_path / "codex"
    executable.write_text("""#!/usr/bin/env python3
import pathlib, sys
if 'features' in sys.argv:
    flags = [sys.argv[i+1] for i,x in enumerate(sys.argv[:-1]) if x == '--disable']
    print('\\n'.join(x+' stable false' for x in flags))
else:
    target = pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])
    with target.open('wb') as stream: stream.truncate(9*1024*1024)
""")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _verified.cache_clear()
    with pytest.raises(ValueError, match="budget"):
        execute(
            {"request_id": "r", "kind": "probe", "packet": {}, "response_schema": {}},
            {"provider": "codex", "model": "fixture"},
        )
    assert not list(tmp_path.glob("anaxigraph-worker-*"))
    _verified.cache_clear()
