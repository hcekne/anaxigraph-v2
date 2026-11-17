"""Connect reviewed findings to plan accountability without another state store."""

from __future__ import annotations

from typing import Any

from anaxigraph.analysis.architecture_guidance import assess_proposals, coverage
from anaxigraph.analysis.architecture_verification import (
    boundary_delta,
    independent_verification,
    obligation,
)
from anaxigraph.analysis.fields import fingerprint, short
from anaxigraph.indexing.package_metadata import capture_package_metadata


def package_context(app: Any, graph) -> dict:
    token = tuple(
        (path, graph.snapshot.files.get(path)) for path in ("pyproject.toml", "package.json")
    )
    cached = getattr(app, "_package_context_cache", None)
    if cached is None or cached[0] != token:
        cached = (token, capture_package_metadata(app.root, graph.snapshot.files))
        app._package_context_cache = cached
    return cached[1]


def owner_reviews(app: Any, graph, owners: tuple[str, ...], kinds: tuple[str, ...]) -> list[dict]:
    return [
        app._review_state(row, graph)
        for kind in kinds
        for row in app.store.reviews(str(app.root), kind, include_requests=False, module_ids=owners)
    ]


def assess(
    app: Any, brief: dict, files: list[str], decisions: Any, structural: list[dict] | None = None
) -> dict:
    """Assess current material reviews; structural observations remain advisory."""
    graph = app._current()
    owners = {item["id"] for item in brief["likely_owners"]}
    owners.update(node.id for node in graph.nodes if node.kind == "module" and node.path in files)
    rows = owner_reviews(app, graph, tuple(sorted(owners)), ("architecture", "patterns"))
    saved = app.store.get_review(str(app.root), "current-plan", "plan")
    prior = saved["result"] if saved else {}
    checkpoint = assess_proposals(rows, owners, graph, prior, decisions, structural) | {
        "snapshot_id": graph.snapshot.id,
        "baseline_id": brief["baseline_id"],
        "task_context": brief["task_context"],
        "files": files,
    }
    result = checkpoint["view"]
    app._require_live(graph)
    app.store.save_review(
        str(app.root),
        graph.snapshot.id,
        "current-plan",
        "plan",
        checkpoint,
        expected_digest=fingerprint(prior if saved else None),
    )
    event = app.evidence.record(
        "architecture_decisions",
        {
            "snapshot_id": graph.snapshot.id,
            "baseline_id": brief["baseline_id"],
            "operation": "assess_plan",
            "status": result["status"],
            "metrics": {key: result[key] for key in ("total", "remaining", "resolved")}
            | {
                "planned_files": len(files),
                "decisions": [
                    item | {"reason": short(item["reason"], 200)} for item in decisions or []
                ],
            },
        },
    )
    return result | {
        "recorded": True,
        "audit_recorded": event is not None,
        "scout_coverage": coverage(rows, owners),
    }


def followthrough(app: Any, baseline_id: str | None, graph, previous, checks: list[dict]) -> dict:
    observed = {
        "boundary_changes": boundary_delta(graph, previous),
        "independent_verification": independent_verification(graph, previous, checks),
    }
    saved = app.store.get_review(str(app.root), "current-plan", "plan")
    event = saved["result"] if saved else app.evidence.latest("architecture_plan")
    if event is None or (not saved and "obligations" not in event.get("metrics", {})):
        return observed | {"status": "not_assessed", "implementation_status": "unverified"}
    if (
        event.get("baseline_id") != baseline_id
        or previous is None
        or previous.snapshot.id != baseline_id
    ):
        return observed | {"status": "baseline_changed", "implementation_status": "unverified"}
    metrics = event.get("view", event.get("metrics", {}))
    obligations = (
        [obligation(item) for item in (*metrics["requirements"], *metrics["advisory"])]
        if "view" in event
        else metrics.get("obligations", [])
    )
    verified = [
        item | {"status": "unresolved", "proposal_status": "unresolved"} for item in obligations
    ]
    return observed | {
        "status": metrics.get("status", event.get("status", "not_assessed")),
        "assessed_snapshot_id": event["snapshot_id"],
        **{key: value for key, value in metrics.items() if key not in {"requirements", "advisory"}},
        "obligations": verified,
        "implementation_status": "unverified",
        "note": "Dispositions record intent. Observed source changes do not establish proposal implementation or behavior.",
    }
