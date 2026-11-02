"""Selective refresh executes real persistence with explicitly synthetic model answers."""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_workflows import answer, app

from anaxigraph.workflows.engine import WorkflowRunner


def fixture_config(model="fixture"):
    return {"stages": {"annotate": {"provider": "codex", "model": model}}}


def test_refresh_skips_unrelated_current_modules_and_defaults_to_production(tmp_path):
    application = app(tmp_path)
    (tmp_path / "test_unit.py").write_text("def test_operation():\n    assert True\n")
    calls = []

    def provider(request, _worker, **_kwargs):
        calls.extend(node["path"] for node in request["packet"]["modules"])
        return answer(request)

    runner = WorkflowRunner(application, provider)
    first = runner.start("mapping", fixture_config(), background=False)
    assert first["status"] == "completed", first
    assert sorted(calls) == ["unit0.py", "unit1.py"]
    assert first["semantic_refresh"]["skipped_modules"] == 1
    repeated = runner.start("mapping", fixture_config(), background=False)
    assert repeated["status"] == "completed", repeated
    assert repeated["total_batches"] == 0
    assert repeated["semantic_refresh"]["reused_modules"] == 2
    assert len(calls) == 2
    (tmp_path / "unit0.py").write_text("def operation_0():\n    return 99\n")
    edited = runner.start("mapping", fixture_config(), background=False)
    assert edited["status"] == "completed", edited
    assert calls == [*calls[:2], "unit0.py"]
    assert edited["semantic_refresh"]["needed_modules"] == 1
    assert edited["semantic_refresh"]["reused_modules"] == 1


def test_explicit_scope_tests_and_changed_model_pipeline_survive(tmp_path):
    application = app(tmp_path, count=1)
    (tmp_path / "test_unit.py").write_text("def test_operation():\n    assert True\n")
    calls = []

    def provider(request, worker, **_kwargs):
        calls.append((request["packet"]["modules"][0]["path"], worker["model"]))
        return answer(request)

    runner = WorkflowRunner(application, provider)
    chosen = runner.start("mapping", fixture_config(), ["test_unit.py"], background=False)
    assert chosen["status"] == "completed", chosen
    full = runner.start("mapping", fixture_config() | {"include_tests": True}, background=False)
    assert full["status"] == "completed", full
    assert sorted(calls) == [("test_unit.py", "fixture"), ("unit0.py", "fixture")]
    revised = runner.start("mapping", fixture_config("different"), background=False)
    assert revised["status"] == "completed", revised
    assert calls[-1] == ("unit0.py", "different")
    assert revised["semantic_refresh"]["reasons"]["model_pipeline_changed"] == 1
    with pytest.raises(ValueError, match="include_tests"):
        runner.configuration({"include_tests": "yes"})


def test_benchmark_refresh_uses_real_runner_cache_and_configured_review_cadence(
    tmp_path, monkeypatch
):
    path = Path(__file__).resolve().parents[1] / "experiments/feature_drift/runner.py"
    spec = importlib.util.spec_from_file_location("selective_feature_drift_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    application = app(tmp_path)
    calls, kinds = Counter(), []

    def provider(request, _worker, **_kwargs):
        calls[request["kind"]] += 1
        return answer(request)

    config_path = tmp_path / "workflow-config.json"
    config_path.write_text(json.dumps(fixture_config()))
    config = SimpleNamespace(
        workflow_config=config_path, review_steps={2}, review_kinds=("patterns",)
    )

    def runtime(_config, _repo, *args):
        if args[0] == "scan":
            return application.scan()
        assert args[:1] == ("run",)
        kinds.append(args[1])
        return WorkflowRunner(application, provider).start(
            args[1], json.loads(config_path.read_text()), background=False
        )

    monkeypatch.setattr(module, "runtime", runtime)
    first = module.refresh_anaxigraph(config, tmp_path, 1)
    assert first["mapping"]["model_calls"] == 2
    reviewed = module.refresh_anaxigraph(config, tmp_path, 2)
    assert reviewed["mapping"]["model_calls"] == 0
    assert reviewed["mapping"]["semantic_refresh"]["reused_modules"] == 2
    assert kinds == ["mapping", "mapping", "patterns"]
    assert calls["delivery"] == 1  # Patterns already schedules CI/CD exactly once.
    total = sum(calls.values())
    repeated = module.refresh_anaxigraph(config, tmp_path, 2)
    assert sum(calls.values()) == total
    assert repeated["patterns"]["model_calls"] == 0
    config.review_kinds = ("fresh-eyes",)
    fresh = module.refresh_anaxigraph(config, tmp_path, 2)
    assert fresh["fresh-eyes"]["status"] == "completed", fresh
    assert calls["fresh-eyes"] == 2  # Independent subsystem plus product alternatives.


@pytest.mark.parametrize("kind", ["mapping", "architecture", "patterns"])
def test_empty_production_scope_finishes_without_mapping_tests_or_markers(tmp_path, kind):
    (tmp_path / "__init__.py").write_text("pass\n")
    (tmp_path / "test_only.py").write_text("def test_case():\n    assert True\n")
    application = app(tmp_path, count=0)
    calls = []

    def provider(request, _worker, **_kwargs):
        calls.append(request["kind"])
        return answer(request)

    result = WorkflowRunner(application, provider).start(kind, background=False)
    assert result["status"] == "completed", result
    assert result["semantic_refresh"]["scope_count"] == 0
    assert result["semantic_refresh"]["needed_modules"] == 0
    assert result["semantic_refresh"]["skipped_modules"] == 2
    assert result["reconciliation"]["declared_modules"] == 0
    assert calls == (["delivery"] if kind == "patterns" else [])
    if kind == "mapping":
        assert result["reconciliation"]["annotated_modules"] == 0


def test_status_distinguishes_complete_production_mapping_from_explicit_full_mapping(tmp_path):
    application = app(tmp_path, count=1)
    (tmp_path / "__init__.py").write_text("pass\n")
    (tmp_path / "test_only.py").write_text("def test_case():\n    assert True\n")

    def provider(request, _worker, **_kwargs):
        result = answer(request)
        for annotation, module in zip(result["annotations"], request["packet"]["modules"]):
            if module["data"]["inert_module"]:
                annotation["capabilities"] = []
        return result

    runner = WorkflowRunner(application, provider)
    assert runner.start("mapping", background=False)["status"] == "completed"
    status = application.status()
    assert status["annotations"] == {"current": 1, "stale": 0, "missing": 2}
    assert status["semantic_scope"] == {
        "eligible_modules": 1,
        "current": 1,
        "stale": 0,
        "missing": 0,
        "outside_scope": 2,
        "outside_current": 0,
    }
    assert (
        runner.start("mapping", {"include_tests": True}, background=False)["status"] == "completed"
    )
    full = application.status()
    assert full["annotations"] == {"current": 3, "stale": 0, "missing": 0}
    assert full["semantic_scope"] == status["semantic_scope"] | {"outside_current": 2}
    (tmp_path / "unit0.py").write_text("def operation_0():\n    return 99\n")
    application.scan()
    assert application.status()["semantic_scope"] == full["semantic_scope"] | {
        "current": 0,
        "stale": 1,
    }
