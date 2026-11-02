"""Connect scout evidence to plan accountability without another state store."""

from __future__ import annotations

from typing import Any

from anaxigraph.analysis.architecture_guidance import coverage, decide, proposals
from anaxigraph.analysis.architecture_verification import (
    boundary_delta,
    independent_verification,
    obligation,
    verify_obligations,
)
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


def owner_reviews(app: Any, graph, owners: tuple[str, ...], *, limit: int = 6) -> list[dict]:
    return [
        app._review_state(row, graph)
        for row in app.store.reviews(
            str(app.root), "architecture", include_requests=False, module_ids=owners, limit=limit
        )
    ]


def assess(
    app: Any, brief: dict, files: list[str], decisions: Any, structural: list[dict] | None = None
) -> dict:
    """Structural extension-point findings come first: they are observed, not model proposals."""
    graph = app._current()
    owners = {item["id"] for item in brief["likely_owners"]}
    owners.update(node.id for node in graph.nodes if node.kind == "module" and node.path in files)
    rows = owner_reviews(app, graph, tuple(sorted(owners)), limit=19)
    scouted = [item for item in proposals(rows, owners, 57) if item["opportunity"] >= 2]
    material = [*(structural or []), *scouted]
    result = decide(material[:12], decisions, truncated=len(rows) > 18 or len(material) > 12)
    event = app.evidence.record(
        "architecture_plan",
        {
            "snapshot_id": graph.snapshot.id,
            "baseline_id": brief["baseline_id"],
            "operation": "assess_plan",
            "files": files,
            "status": result["status"],
            "metrics": {key: result[key] for key in ("decisions", "pending_ids", "truncated")}
            | {"obligations": [obligation(item) for item in result["requirements"]]},
        },
    )
    return result | {"recorded": event is not None, "scout_coverage": coverage(rows, owners)}


def followthrough(app: Any, baseline_id: str | None, graph, previous, checks: list[dict]) -> dict:
    observed = {
        "boundary_changes": boundary_delta(graph, previous),
        "independent_verification": independent_verification(graph, previous, checks),
    }
    event = app.evidence.latest("architecture_plan")
    if event is None:
        return observed | {"status": "not_assessed", "implementation_status": "unverified"}
    if (
        event.get("baseline_id") != baseline_id
        or previous is None
        or previous.snapshot.id != baseline_id
    ):
        return observed | {"status": "baseline_changed", "implementation_status": "unverified"}
    metrics = event["metrics"]
    obligations = metrics.get("obligations", [])
    sources = (
        app.indexer.captured_sources(graph.snapshot)
        if any(item["predicate"] == "no-static-registrar-enumeration" for item in obligations)
        else {}
    )
    verified = verify_obligations(obligations, graph, previous, sources)
    return observed | {
        "status": event["status"],
        "assessed_snapshot_id": event["snapshot_id"],
        **metrics,
        "obligations": verified,
        "implementation_status": "unverified",
        "note": "Dispositions record intent. Per-obligation predicates do not establish full proposal implementation or behavior.",
    }
