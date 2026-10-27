"""Schedule independent architecture and CI reviews using the resumable batch engine."""

from __future__ import annotations

from anaxigraph.analysis.architecture import LENSES
from anaxigraph.analysis.packets import plan_scopes
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
    if kind == "delivery":
        return ["delivery"]
    stages: list[str | tuple[str, ...]] = ["annotate"]
    if kind == "patterns":
        screened = run["config"].get("pattern_mode") == "screened"
        discovery = []
        if run.get("architecture_scouts_version") == 1:
            discovery.append("architecture")
        if run.get("delivery_review_version") == 1:
            discovery.append("delivery")
        if screened:
            discovery.append("pattern-screen")
        if discovery:
            stages.append(tuple(discovery))
        stages.append("patterns")
    elif kind != "annotate":
        stages.append(kind)
    return stages


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


def _review_ids(run, excluded=("annotate",)):
    return [
        batch["request_id"]
        for batch in run["batches"]
        if batch.get("request_id") and batch["kind"] not in excluded
    ]


def _reconcile(runner, run):
    kind = run["kind"]
    run["phase"] = "Final reconciliation"
    runner._save(run)
    if kind == "fresh-eyes":
        runner._product(run)
    review_ids = _review_ids(run)
    reports = _component_reports(runner, run)
    reconciliation = (
        reports[kind]
        if kind in reports
        else runner.application.analysis_reconcile(
            kind,
            _review_ids(run, ("annotate", "architecture", "delivery")),
            run["scope"],
        )
    )
    assert reconciliation is not None
    run["reconciliation"] = reconciliation | {
        "status": "completed",
        "phase": "Final reconciliation",
        "review_ids": review_ids,
        "message": "Validated batches account for the declared analysis scope; this does not report executed project checks.",
    }
    if kind == "patterns":
        run["reconciliation"].update(reports)


def _component_reports(runner, run):
    reports = {}
    for kind, marker in (
        ("architecture", "architecture_scouts_version"),
        ("delivery", "delivery_review_version"),
    ):
        if run["kind"] == kind or (run["kind"] == "patterns" and run.get(marker) == 1):
            reports[kind] = runner.application.analysis_reconcile(
                kind,
                [batch["request_id"] for batch in run["batches"] if batch["kind"] == kind],
                [] if kind == "delivery" else run["scope"],
            )
    return reports
