"""Schedule independent architecture and CI reviews using the resumable batch engine."""

from __future__ import annotations

from anaxigraph.analysis.architecture import LENSES
from anaxigraph.analysis.packets import plan_scopes
from anaxigraph.analysis.patterns import production_modules
from anaxigraph.analysis.validation import validate_result
from anaxigraph.workflows.progress import plan_batch


def consumable_saved(application, request_id, kind):
    if kind != "fresh-eyes":
        return True
    request = application.store.get_review(str(application.root), request_id, "request")
    row = application.store.get_review(str(application.root), request_id, "fresh-eyes")
    if request is None or row is None:
        return False
    result = row["result"]["result"]
    if any(
        field not in result
        for field in ("functional_summary", "functional_requirements", "functional_coverage")
    ):
        return False
    try:
        graph = application._current()
        validate_result(request["result"], result, graph)
    except ValueError:
        return False
    return True


def repair_fresh(application, run):
    invalid: set[str] = set()
    for batch in run["batches"]:
        if batch["kind"] == "fresh-eyes" and batch["status"] == "completed":
            if invalid.intersection(batch.get("children", [])) or not consumable_saved(
                application, batch["request_id"], "fresh-eyes"
            ):
                batch["status"] = "pending"
                batch.pop("reused", None)
                invalid.add(batch["request_id"])


def review_choices(run, kind):
    choices = run["config"]["review_passes"]
    stage = f"{kind}-review"
    if kind in {"architecture", "delivery"} and not choices and stage in run["config"]["stages"]:
        return [run["config"]["stages"][stage]]
    return choices


def linked_products(run):
    known: set[str] = set()
    for batch in list(run["batches"]):
        if "children" in batch and not set(batch["children"]) <= known:
            run["batches"].remove(batch)  # Saved reviews remain; obsolete request links do not.
        elif batch["kind"] == "fresh-eyes" and batch.get("request_id"):
            known.add(batch["request_id"])


def finding_review_policy(config):
    if config["review_passes"]:
        return "Ordered passes replace dedicated architecture and CI/CD finding reviewers; positive findings only."
    stages = [kind for kind in ("architecture", "delivery") if f"{kind}-review" in config["stages"]]
    return (
        "Dedicated positive-only finding reviewers: "
        + ", ".join(stages).replace("delivery", "CI/CD")
        if stages
        else "No dedicated finding reviewer in this saved configuration."
    )


def applied_reviewers(kind, result, reviewers):
    field = {"architecture": "findings", "delivery": "recommendations"}.get(kind)
    return reviewers if field is None or result.get(field) else []


def plan_scouts(runner, run):
    for scope in plan_scopes(runner.application._current(), "architecture", run["scope"]):
        for lens in LENSES:
            plan_batch(run, "architecture", scope, lens=lens)


def _stages(run):
    kind = run["kind"]
    if kind in {"annotate", "delivery"}:
        return [kind]
    if kind != "patterns":
        return ["annotate", kind]
    discovery = _component_stages(run)
    if run["config"].get("pattern_mode") == "screened":
        discovery.append("pattern-screen")
    stages: list[str | tuple[str, ...]] = ["annotate"]
    if discovery:
        stages.append(tuple(discovery))
    return stages + ["patterns"]


def review_workflow(runner, run):
    if run["kind"] == "fresh-eyes":
        repair_fresh(runner.application, run)
    for stage in _stages(run):
        runner._stage(run, stage)
        if any(batch["status"] == "failed" for batch in run["batches"]):
            raise ValueError(
                "Some batches failed; completed results are saved. Retry failed batches."
            )
    if run["kind"] == "fresh-eyes":
        linked_products(run)
    _reconcile(runner, run)


def _review_ids(run, kinds=None):
    return [
        batch["request_id"]
        for batch in run["batches"]
        if batch.get("request_id")
        and batch["kind"] != "annotate"
        and (kinds is None or batch["kind"] in kinds)
    ]


def _reconcile(runner, run):
    kind = run["kind"]
    run["phase"] = "Final reconciliation"
    runner._save(run)
    if kind == "fresh-eyes":
        runner._product(run)
    reports = {}
    for stage in dict.fromkeys(_component_stages(run) + [kind]):
        reports[stage] = runner.application.analysis_reconcile(
            stage,
            _review_ids(run, {stage, "pattern-screen"} if stage == "patterns" else {stage}),
            [] if stage == "delivery" else run["scope"],
        )
    run["reconciliation"] = reports.pop(kind) | {
        "review_ids": _review_ids(run),
        "message": "Validated batches account for the declared analysis scope; this does not report executed project checks.",
    }
    if kind == "patterns":
        run["reconciliation"].update(reports)


def _component_stages(run):
    return [
        kind
        for kind, marker in (
            ("architecture", "architecture_scouts_version"),
            ("delivery", "delivery_review_version"),
        )
        if run["kind"] == kind or (run["kind"] == "patterns" and run.get(marker) == 1)
    ]


def _mapping_scope(graph, run, planner):
    scope = run["scope"]
    if scope:
        # Validate identities even when every selected module is current.
        planner(graph, "annotate", scope, include_current=True)
        return [n for n in graph.nodes if n.kind == "module" and (n.id in scope or n.path in scope)]
    if run["config"].get("include_tests"):
        return [n for n in graph.nodes if n.kind == "module"]
    return production_modules(graph)


def _refresh_modules(selected, pipeline, require_pipeline):
    needed, reused = [], 0
    reasons: dict[str, int] = {}
    for node in selected:
        annotation = node.data.get("annotation") or {}
        reason = node.data.get("annotation_status", "missing")
        if reason == "current":
            recorded = (annotation.get("execution") or {}).get("pipeline")
            if recorded == pipeline or not require_pipeline:
                reused += 1
                continue
            reason = "model_pipeline_changed"
        needed.append(node.id)
        reasons[reason] = reasons.get(reason, 0) + 1
    return needed, reused, reasons


def _mapping_plan(runner, run, planner, select_worker):
    graph = runner.application._current()
    selected = _mapping_scope(graph, run, planner)
    scope = [node.id for node in selected]
    if run["kind"] in {"annotate", "fresh-eyes"} and run["scope"] is None:
        run["scope"] = scope
    choice = run["config"]["stages"]["annotate"]
    pipeline = {
        "worker": select_worker(choice)
        | {"fallbacks": [select_worker(x) for x in choice.get("fallbacks", [])]},
        "review_passes": [select_worker(x) for x in review_choices(run, "annotate")],
    }
    needed, reused, reasons = _refresh_modules(
        selected, pipeline, run.get("mapping_pipeline_required", False)
    )
    excluded = sum(n.kind == "module" for n in graph.nodes) - len(selected)
    run["semantic_refresh"] = {
        "scope": scope[:20],
        "scope_count": len(scope),
        "scope_truncated": len(scope) > 20,
        "needed_modules": len(needed),
        "reused_modules": reused,
        "skipped_modules": excluded,
        "reasons": reasons | {"current_compatible": reused, "outside_scope": excluded},
    }
    # Ordered-review runs retain their existing batch accounting and reuse receipts.
    return scope if run["config"]["review_passes"] else needed


def plan_workflow_scopes(runner, run, kind, planner, select_worker):
    scope = None if kind == "delivery" and run["kind"] != "delivery" else run["scope"]
    if kind == "annotate":
        scope = _mapping_plan(runner, run, planner, select_worker)
        if not scope:
            return
    for group in planner(
        runner.application._current(), kind, scope, include_current=kind == "annotate"
    ):
        options = {}
        if kind == "delivery":
            options["pipeline_paths"] = run["scope"] if run["kind"] == "delivery" else None
        plan_batch(run, kind, group, **options)
