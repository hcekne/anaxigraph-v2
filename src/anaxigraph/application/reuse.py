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
        cache = (graph, analysis_contexts(graph))
        app._review_context_cache = cache
    return cache[1](scope)


def _compatible_context(app: Any, graph: Graph, row: dict[str, Any], scope: list[str]) -> bool:
    recorded = row["result"].get("resource_context")
    if recorded is None:
        previous = app.store.load_graph(str(app.root), row["snapshot_id"])
        if previous is None:
            return False
        recorded = analysis_contexts(previous)(scope)
    return recorded == review_context(app, graph, scope)


def reuse_analysis(app: Any, request: dict[str, Any], pipeline: dict[str, Any] | None) -> bool:
    if request["kind"] not in {"pattern-screen", "patterns"}:
        return False
    row = app.store.find_review(
        str(app.root), request["kind"], request["scope"], request.get("request_options", {})
    )
    if row is None:
        return False
    body = row["result"]
    if body.get("input_identity") != input_identity(request):
        return False
    result = body["result"]
    if pipeline is not None and result.get("execution", {}).get("pipeline") != pipeline:
        return False
    graph = app._current()
    if not _compatible_context(app, graph, row, request["scope"]):
        return False
    try:
        validate_result(request, result, graph)
    except ValueError:
        return False
    provenance = body.get("provenance") or {
        "original_id": row["id"],
        "source_snapshot": row["snapshot_id"],
    }
    app.store.save_review(
        str(app.root),
        graph.snapshot.id,
        request["request_id"],
        request["kind"],
        body | {"provenance": provenance},
    )
    return True
