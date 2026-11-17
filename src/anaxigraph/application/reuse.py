"""Reuse identical scoped analysis after a checkout changes, without a model call."""

from __future__ import annotations

from typing import Any

from anaxigraph.analysis.portable import input_identity
from anaxigraph.analysis.validation import validate_result
from anaxigraph.contracts import Graph
from anaxigraph.storage.portable import analysis_contexts


def review_context(app: Any, graph: Graph, scope: list[str]) -> str:
    cache = getattr(app, "_review_context_cache", None)
    if cache is None or cache[0] is not graph:
        cache = app._review_context_cache = (graph, analysis_contexts(graph))
    return cache[1](scope)


def _compatible_context(app: Any, graph: Graph, row: dict[str, Any], scope: list[str]) -> bool:
    recorded = row["result"].get("resource_context")
    if recorded is None:
        previous = app.store.load_graph(str(app.root), row["snapshot_id"])
        if previous is None:
            return False
        recorded = analysis_contexts(previous)(scope)
    return recorded == review_context(app, graph, scope)


def applicable_request(app: Any, row: dict, graph: Graph, request=None) -> dict | None:
    """Validate saved content against current scoped inputs without rewriting its capture."""
    body = row["result"]
    try:
        request = request or app._rebuild_request(graph, row["kind"], body)
        if request["snapshot_id"] != graph.snapshot.id:
            return None
        if row["id"] != request["request_id"] and (
            body.get("input_identity") != input_identity(request)
            or request["kind"] != "delivery"
            and not _compatible_context(app, graph, row, request["scope"])
        ):
            return None
        if [item.get("result_digest") for item in body.get("children", [])] != [
            item.get("result_digest") for item in request.get("children", [])
        ]:
            return None
        validate_result(request, body.get("result"), graph)
    except ValueError:
        return None
    return request


def retain_review(app: Any, graph: Graph, row: dict, request: dict) -> None:
    """Publish checked reuse or retention intent while preserving its original provenance."""
    body = dict(row["result"])
    identity, kind = request["request_id"], request["kind"]
    mode = "selected" if body.get("pattern_mode") == "selected" else request.get("pattern_mode")
    if row["id"] == identity and mode == body.get("pattern_mode"):
        return
    body["provenance"] = body.get("provenance") or {
        "original_id": row["id"],
        "source_snapshot": row["snapshot_id"],
    }
    if mode in ("screened", "selected"):
        body["pattern_mode"] = mode
    app.store.save_review(str(app.root), graph.snapshot.id, identity, kind, body)


def pipeline_matches(result: dict, pipeline: dict | None) -> bool:
    return pipeline is None or (result.get("execution") or {}).get("pipeline") == pipeline


def reuse_analysis(app: Any, request: dict[str, Any], pipeline: dict[str, Any] | None) -> bool:
    if request["kind"] not in {"pattern-screen", "patterns", "architecture", "delivery"}:
        return False
    row = app.store.find_review(
        str(app.root),
        request["kind"],
        request["scope"],
        request.get("request_options", {}),
        pattern_mode=request.get("pattern_mode"),
    )
    graph = app._current()
    if row is None or applicable_request(app, row, graph, request) is None:
        return False
    if not pipeline_matches(row["result"]["result"], pipeline):
        return False
    retain_review(app, graph, row, request)
    return True
