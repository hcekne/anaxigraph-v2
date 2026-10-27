"""Rejected model output is repairable; local failures never buy a stronger retry."""

from __future__ import annotations

import copy
import json
import sqlite3

import pytest
from test_workflows import answer, app

from anaxigraph.analysis import packets, validation
from anaxigraph.workflows import engine
from anaxigraph.workflows.engine import WorkflowRunner

BRIEF = ("functional_summary", "functional_requirements", "functional_coverage")


@pytest.mark.parametrize("rescan", [False, True])
def test_source_edit_stops_before_paid_review_or_fallback(tmp_path, rescan):
    application = app(tmp_path, count=1)
    calls = []

    def provider(request, worker, **_kwargs):
        calls.append(worker["model"])
        if len(calls) == 1:
            path = application.root / "unit0.py"
            path.write_text(path.read_text() + "\n# concurrent edit\n")
            if rescan:
                application.scan()
        return answer(request)

    config = {
        "stages": {
            "annotate": {
                "provider": "codex",
                "model": "weak",
                "fallbacks": [{"provider": "codex", "model": "strong"}],
            }
        },
        "review_passes": [{"provider": "codex", "model": "reviewer"}],
    }
    runner = WorkflowRunner(application, provider)
    failed = runner.start("mapping", config, background=False)
    assert failed["status"] == "failed" and calls == ["weak"]
    resumed = runner.resume(failed["run_id"], background=False)
    assert resumed["status"] == "failed" and calls == ["weak"]
    restarted = runner.start("mapping", config, background=False)
    assert restarted["status"] == "completed", restarted
    assert calls == ["weak", "weak", "reviewer"]


@pytest.mark.parametrize("field", BRIEF)
def test_current_fresh_contract_rejects_missing_product_brief_before_save(tmp_path, field):
    application = app(tmp_path, count=1)
    application.scan()
    request = application.analysis_request("annotate")
    application.submit_analysis(request["request_id"], answer(request))
    fresh = application.analysis_request("fresh-eyes")
    result = answer(fresh)
    result.pop(field)
    with pytest.raises(validation.ModelOutputError, match=field):
        application.submit_analysis(fresh["request_id"], result)
    assert application.reviews("fresh-eyes")["total"] == 0


def test_incomplete_subsystem_uses_stronger_output_once_then_reaches_product(tmp_path):
    application = app(tmp_path, count=1)
    calls = []

    def provider(request, worker, **_kwargs):
        result = answer(request)
        if request["kind"] == "fresh-eyes":
            calls.append((request["packet"].get("level", "subsystem"), worker["model"]))
            if worker["model"] == "weak" and request["packet"].get("level") != "product":
                result.pop("functional_coverage")
        return result

    config = {
        "stages": {
            "fresh-eyes": {
                "provider": "codex",
                "model": "weak",
                "fallbacks": [{"provider": "codex", "model": "strong"}],
            }
        }
    }
    result = WorkflowRunner(application, provider).start("fresh-eyes", config, background=False)
    assert result["status"] == "completed", result
    assert calls == [("subsystem", "weak"), ("subsystem", "strong"), ("product", "weak")]
    assert application.reviews("fresh-eyes")["total"] == 2


@pytest.mark.parametrize("resume", [True, False])
@pytest.mark.parametrize("old_schema", [True, False])
def test_legacy_incomplete_saved_child_is_repaired_without_repeating_valid_mapping(
    tmp_path, monkeypatch, resume, old_schema
):
    application = app(tmp_path, count=1)
    original = validation._fresh_eyes
    calls = []

    def provider(request, _worker, **_kwargs):
        calls.append(request["kind"])
        result = answer(request)
        if request["kind"] == "fresh-eyes":
            for field in BRIEF:
                result.pop(field)
        return result

    # Reproduce a result accepted by the former validator, followed by its real
    # failed product construction. Keep its actual persisted request/run state.
    with monkeypatch.context() as legacy:
        if old_schema:
            packet_factory = packets._fresh_packet

            def legacy_packet(*args):
                packet, schema = packet_factory(*args)
                return packet, {key: value for key, value in schema.items() if key not in BRIEF}

            legacy.setattr(packets, "_fresh_packet", legacy_packet)
        else:
            legacy.setattr(
                validation,
                "_fresh_eyes",
                lambda request, result, graph: original(
                    request | {"response_schema": {}}, result, graph
                ),
            )
        runner = WorkflowRunner(application, provider)
        failed = runner.start("fresh-eyes", background=False)
    assert failed["status"] == "failed"
    assert application.reviews("fresh-eyes")["total"] == 1
    assert calls == ["annotate", "fresh-eyes"]
    calls.clear()

    def repaired(request, *_args, **_kwargs):
        calls.append(request["kind"])
        return answer(request)

    runner = WorkflowRunner(application, repaired)
    result = (
        runner.resume(failed["run_id"], background=False)
        if resume
        else runner.start("fresh-eyes", background=False)
    )
    assert result["status"] == "completed", result
    assert calls == ["fresh-eyes", "fresh-eyes"]
    assert application.status()["annotations"]["current"] == 1
    assert application.reviews("fresh-eyes")["total"] == 2
    before = list(calls)
    assert runner.start("fresh-eyes", background=False)["status"] == "completed"
    assert calls == before


@pytest.mark.parametrize("declared", [(), ("functional_summary",), BRIEF[:2]])
def test_old_standalone_three_alternative_protocol_remains_valid(tmp_path, declared):
    application = app(tmp_path, count=1)
    application.scan()
    request = application.analysis_request("annotate")
    application.submit_analysis(request["request_id"], answer(request))
    public = application.analysis_request("fresh-eyes")
    request = application.store.get_review(str(application.root), public["request_id"], "request")[
        "result"
    ]
    result = answer(public)
    for field in BRIEF:
        if field not in declared:
            request["response_schema"].pop(field)
            result.pop(field)
    validation.validate_result(request, result, application._current())
    if declared:
        result.pop(declared[0])
        with pytest.raises(validation.ModelOutputError, match=declared[0]):
            validation.validate_result(request=request, result=result, graph=application._current())


@pytest.mark.parametrize("changed_brief", [False, True])
def test_repaired_child_reuses_only_a_product_for_its_exact_brief(tmp_path, changed_brief):
    application = app(tmp_path, count=1)
    calls = []
    repairing = False

    def provider(request, *_args, **_kwargs):
        calls.append(request["kind"])
        result = answer(request)
        if (
            repairing
            and changed_brief
            and request["kind"] == "fresh-eyes"
            and request["packet"].get("level") != "product"
        ):
            result["functional_requirements"] = ["Deliver a reliable response for each caller."]
        return result

    runner = WorkflowRunner(application, provider)
    first = runner.start("fresh-eyes", background=False)
    assert first["status"] == "completed", first
    path = runner._path(first["run_id"])
    state = json.loads(path.read_text())
    child = next(
        batch
        for batch in state["batches"]
        if batch["kind"] == "fresh-eyes" and "children" not in batch
    )
    row = application.store.get_review(str(tmp_path), child["request_id"], "fresh-eyes")
    row["result"]["result"].pop("functional_coverage")
    application.store.save_review(
        str(tmp_path), row["snapshot_id"], row["id"], "fresh-eyes", row["result"]
    )
    (tmp_path / "anaxigraph/record.json").unlink()
    state["status"] = "failed"
    path.write_text(json.dumps(state))
    calls.clear()
    repairing = True
    repaired = runner.resume(first["run_id"], background=False)
    assert repaired["status"] == "completed", repaired
    assert calls == ["fresh-eyes"] * (2 if changed_brief else 1)


def test_non_object_provider_output_uses_the_configured_stronger_fallback(tmp_path):
    calls = []

    def provider(request, worker, **_kwargs):
        calls.append(worker["model"])
        return [] if worker["model"] == "weak" else answer(request)

    result = WorkflowRunner(app(tmp_path, count=1), provider).start(
        "mapping", fallback(), background=False
    )
    assert result["status"] == "completed", result
    assert calls == ["weak", "strong"]


def test_changed_saved_input_does_not_blame_or_escalate_the_current_product_model(tmp_path):
    application = app(tmp_path, count=1)
    calls = []

    def provider(request, worker, **_kwargs):
        if request["kind"] == "fresh-eyes":
            calls.append((request["packet"].get("level", "subsystem"), worker["model"]))
            if request["packet"].get("level") == "product":
                child = application.store.reviews(str(tmp_path), "fresh-eyes")[0]
                child["result"]["result"].pop("functional_coverage")
                application.store.save_review(
                    str(tmp_path), child["snapshot_id"], child["id"], "fresh-eyes", child["result"]
                )
        return answer(request)

    config = {
        "stages": {
            "fresh-eyes": {
                "provider": "codex",
                "model": "weak",
                "fallbacks": [{"provider": "codex", "model": "strong"}],
            }
        }
    }
    result = WorkflowRunner(application, provider).start("fresh-eyes", config, background=False)
    assert result["status"] == "failed"
    assert calls == [("subsystem", "weak"), ("product", "weak")]
    assert result["attempt_failures"][0]["phase"] == "local"


def test_nested_product_resume_relinks_only_the_repaired_branch(tmp_path, monkeypatch):
    application = app(tmp_path, count=17)
    planner = engine.plan_scopes
    monkeypatch.setattr(
        engine,
        "plan_scopes",
        lambda graph, kind, scope=None, **options: planner(
            graph, kind, scope, **(options | {"batch_size": 1} if kind == "fresh-eyes" else options)
        ),
    )
    repairing = False
    calls = []

    def provider(request, *_args, **_kwargs):
        result = answer(request)
        if request["kind"] == "fresh-eyes":
            calls.append(request["packet"].get("level", "subsystem"))
            if repairing and request["packet"].get("level") != "product":
                result["functional_requirements"] = ["Deliver reliable results for each caller."]
        return result

    runner = WorkflowRunner(application, provider)
    first = runner.start("fresh-eyes", background=False)
    assert first["status"] == "completed", first
    assert len(calls) == 20  # 17 subsystem briefs, two bounded parents, one product.
    path = runner._path(first["run_id"])
    run = json.loads(path.read_text())
    child = next(
        batch
        for batch in run["batches"]
        if batch["kind"] == "fresh-eyes" and "children" not in batch
    )
    row = application.store.get_review(str(tmp_path), child["request_id"], "fresh-eyes")
    row["result"]["result"].pop("functional_coverage")
    application.store.save_review(
        str(tmp_path), row["snapshot_id"], row["id"], "fresh-eyes", row["result"]
    )
    (tmp_path / "anaxigraph/record.json").unlink()
    run["status"] = "failed"
    path.write_text(json.dumps(run))
    repairing = True
    calls.clear()
    repaired = runner.resume(first["run_id"], background=False)
    assert repaired["status"] == "completed", repaired
    assert calls == ["subsystem", "product", "product"]
    saved = json.loads(path.read_text())
    identities = [batch["id"] for batch in saved["batches"]]
    assert len(identities) == len(set(identities))


@pytest.mark.parametrize("rescan", [False, True])
def test_edit_during_model_call_fails_locally_without_paid_fallback(tmp_path, rescan):
    application = app(tmp_path, count=1)
    calls = []

    def provider(request, worker, **_kwargs):
        calls.append(worker["model"])
        (tmp_path / "unit0.py").write_text("def operation_0():\n    return 100\n")
        if rescan:
            application.scan()
        return answer(request)

    result = WorkflowRunner(application, provider).start("mapping", fallback(), background=False)
    assert result["status"] == "failed"
    assert calls == ["weak"]
    assert result["failed_attempts"] == 1
    assert result["attempt_failures"][0]["phase"] == "local"
    assert not any(
        row["payload"].get("status") == "rejected"
        for row in application.evidence.events(100)["items"]
        if row["kind"] == "analysis_result"
    )


def fallback():
    return {
        "stages": {
            "annotate": {
                "provider": "codex",
                "model": "weak",
                "fallbacks": [{"provider": "codex", "model": "strong"}],
            }
        }
    }


@pytest.mark.parametrize(
    "failure",
    [
        ValueError("Storage budget exceeded"),
        OSError("Disk full"),
        sqlite3.OperationalError("database or disk is full"),
    ],
)
def test_local_storage_failure_does_not_retry_model_and_can_resume(tmp_path, monkeypatch, failure):
    application = app(tmp_path, count=1)
    calls = []

    def provider(request, worker, **_kwargs):
        calls.append(worker["model"])
        return answer(request)

    def full(*_args, **_kwargs):
        raise failure

    runner = WorkflowRunner(application, provider)
    with monkeypatch.context() as local:
        local.setattr(application.store, "annotate", full)
        failed = runner.start("mapping", fallback(), background=False)
    assert failed["status"] == "failed"
    assert calls == ["weak"]
    assert failed["attempt_failures"][0]["phase"] == "local"
    result = runner.resume(failed["run_id"], background=False)
    assert result["status"] == "completed", result
    assert calls == ["weak", "weak"]


def test_review_override_is_explicit_and_round_trips_without_changing_selected_passes(tmp_path):
    runner = WorkflowRunner(
        app(tmp_path, count=1), lambda request, *_args, **_kwargs: answer(request)
    )
    dedicated = runner.configuration()
    assert "positive-only" in dedicated["effective_finding_review"]
    chosen = [{"provider": "claude", "model": "intentional-reviewer"}]
    config = runner.configuration({"review_passes": chosen})
    assert "replace" in config["effective_finding_review"]
    assert runner.configuration(copy.deepcopy(config))["review_passes"] == chosen
    run = runner.start("mapping", config, background=False)
    assert run["status"] == "completed", run
    assert run["effective_finding_review"] == config["effective_finding_review"]
    assert (
        "effective_finding_review" in json.loads(runner._path(run["run_id"]).read_text())["config"]
    )
