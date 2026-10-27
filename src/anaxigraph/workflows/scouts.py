"""Schedule focused architecture lenses using the existing resumable batch engine."""

from __future__ import annotations

from anaxigraph.analysis.architecture import LENSES
from anaxigraph.analysis.packets import plan_scopes


def plan_scouts(runner, run):
    for scope in plan_scopes(runner.application._current(), "architecture", run["scope"]):
        for lens in LENSES:
            run["batches"].append(
                {
                    "id": len(run["batches"]) + 1,
                    "kind": "architecture",
                    "scope": scope,
                    "lens": lens,
                    "status": "pending",
                    "attempts": 0,
                }
            )


def _stages(run):
    kind = run["kind"]
    scouts = kind == "architecture" or (
        kind == "patterns" and run.get("architecture_scouts_version") == 1
    )
    stages: list[str | tuple[str, ...]] = ["annotate"]
    if kind == "patterns":
        screened = run["config"].get("pattern_mode") == "screened"
        if scouts:
            stages.append(("architecture", "pattern-screen") if screened else "architecture")
        elif screened:
            stages.append("pattern-screen")
        stages.append("patterns")
    elif kind != "annotate":
        stages.append(kind)
    return stages


def review_workflow(runner, run):
    for stage in _stages(run):
        runner._stage(run, stage)
        if any(batch["status"] == "failed" for batch in run["batches"]):
            raise ValueError(
                "Some batches failed; completed results are saved. Retry failed batches."
            )
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
    architecture = None
    if kind == "architecture" or (
        kind == "patterns" and run.get("architecture_scouts_version") == 1
    ):
        architecture = runner.application.analysis_reconcile(
            "architecture",
            [b["request_id"] for b in run["batches"] if b["kind"] == "architecture"],
            run["scope"],
        )
    reconciliation = (
        architecture
        if kind == "architecture"
        else runner.application.analysis_reconcile(
            kind,
            _review_ids(run, ("annotate", "architecture")),
            run["scope"],
        )
    )
    assert reconciliation is not None
    run["reconciliation"] = reconciliation | {
        "status": "completed",
        "phase": "Final reconciliation",
        "review_ids": review_ids,
        "message": "Validated batches form one current map; all declared scopes are accounted for.",
    }
    if kind == "patterns" and architecture is not None:
        run["reconciliation"]["architecture"] = architecture
