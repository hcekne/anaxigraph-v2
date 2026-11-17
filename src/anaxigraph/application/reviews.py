"""Prepare, validate and retain model work through the shared application state."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from anaxigraph.analysis import (
    build_product_request,
    build_request,
    canonical_result,
    validate_result,
)
from anaxigraph.analysis.architecture import reconcile_architecture
from anaxigraph.analysis.architecture_guidance import proposals as scout_proposals
from anaxigraph.analysis.delivery import capture_coverage, reconcile_delivery
from anaxigraph.analysis.fields import fingerprint
from anaxigraph.analysis.packets import SOURCE_REVIEW_KINDS, catalogue, request_modules
from anaxigraph.analysis.patterns import (
    actionable_opportunities,
    excluded_modules,
    production_modules,
    reconcile_patterns,
    review_summary,
)
from anaxigraph.analysis.portable import ANNOTATION_CONTRACT_VERSION, input_identity
from anaxigraph.application import architecture as component_architecture
from anaxigraph.application import delivery
from anaxigraph.application.context import (
    _bounds,
    context_fingerprints,
    dependency_implementation_fingerprints,
)
from anaxigraph.application.reuse import (
    applicable_request,
    pipeline_matches,
    retain_review,
    reuse_analysis,
    review_context,
)
from anaxigraph.contracts import Graph, Node
from anaxigraph.indexing import capture_sources

if TYPE_CHECKING:
    from anaxigraph.application.core import Application


def _screened_metadata(kind: str, options: dict[str, Any]) -> dict[str, str]:
    """Separate retention intent from the inputs sent to a model or fingerprinted."""
    screened = options.pop("screened", False)
    if type(screened) is not bool or (
        screened and (kind != "patterns" or options.get("pattern_ids") is None)
    ):
        raise ValueError("screened must be a boolean for selected pattern details only")
    if kind == "patterns" and options.get("pattern_ids") is not None:
        return {"pattern_mode": "screened" if screened else "selected"}
    return {}


def _request_context(app, graph: Graph, kind: str, options: dict, scope=None) -> dict:
    if kind == "architecture":
        return {"package_context": component_architecture.package_context(app, graph)}
    if kind == "delivery":
        return {
            "delivery_context": delivery.context(app, graph, options.get("pipeline_paths") or scope)
        }
    return {}


def _result_metadata(request: dict) -> dict:
    metadata = {}
    if request["kind"] == "delivery":
        metadata["delivery_coverage"] = capture_coverage(request["packet"]["delivery_context"])
    if request.get("pattern_mode") in ("screened", "selected"):
        metadata["pattern_mode"] = request["pattern_mode"]
    return metadata


def analysis_request(
    app, kind: str, scope: list[str] | None = None, **options: Any
) -> dict[str, Any]:
    metadata = _screened_metadata(kind, options)
    graph = app._current()
    app._require_live(graph)
    sources = None
    options.update(_request_context(app, graph, kind, options, scope))
    if kind == "annotate" or kind in SOURCE_REVIEW_KINDS:
        selected_paths = [node.path for node in request_modules(graph, scope, kind)]
        sources = capture_sources(app.root, selected_paths, graph.snapshot.files)
    request = build_request(graph, kind, scope, sources, **options) | metadata
    stored = {key: value for key, value in request.items() if key != "implementation_evidence"}
    app.store.save_review(
        str(app.root), graph.snapshot.id, request["request_id"], "request", stored
    )
    if kind == "fresh-eyes":
        # Do not hand the blind designer a scope envelope containing module IDs.
        return {key: request[key] for key in ("request_id", "kind", "packet", "response_schema")}
    return {key: value for key, value in request.items() if key != "function_ids"}


def _annotation_cache_data(node: Node, annotation: dict[str, Any], fingerprint: str) -> dict:
    data = dict(node.data)
    data.pop("annotation_stale_reason", None)
    return data | {
        "annotation": annotation,
        "annotation_status": "current",
        "annotation_fingerprint": fingerprint,
        "dependency_implementation_changed": False,
        "behavioral_validation": "not_independently_verified",
    }


def submit_analysis(app: Application, request_id: str, result: dict[str, Any]) -> dict[str, Any]:
    saved = app.store.get_review(str(app.root), request_id, "request")
    if saved is None:
        raise ValueError("Unknown or expired request; request the analysis again")
    request = saved["result"]
    graph = app._current()
    if request["snapshot_id"] != graph.snapshot.id:
        raise ValueError("Request belongs to a stale snapshot; request it again")
    app._require_live(graph)
    result = canonical_result(request, result)
    validate_result(request, result, graph)
    if request["kind"] == "annotate":
        cached_token = (
            app._graph_cache[0]
            if app._graph_cache is not None and app._graph_cache[1] is graph
            else None
        )
        written, updated = 0, {}
        contexts = context_fingerprints(graph)
        implementations = dependency_implementation_fingerprints(graph)
        for annotation in result["annotations"]:
            node = next(n for n in graph.nodes if n.id == annotation["node_id"])
            value = dict(annotation) | {
                "producer": result["producer"],
                "basis": "agent-reported-interpretation",
                "source_snapshot": graph.snapshot.id,
                "context_fingerprint": contexts[node.path],
                "dependency_implementation_fingerprint": implementations[node.path],
                "annotation_contract": ANNOTATION_CONTRACT_VERSION,
                "request_id": request_id,
                "execution": result.get("execution"),
            }
            prior = next(
                n["data"].get("annotation")
                for n in request["packet"]["modules"]
                if n["id"] == node.id
            )
            changed = app.store.annotate(
                str(app.root),
                graph.snapshot.id,
                node.id,
                value,
                expected_digest=fingerprint(prior),
            )
            written += int(changed)
            updated[node.id] = value
        after = app.store.knowledge_token(str(app.root))
        if cached_token is not None and after == (cached_token[0], cached_token[1] + written):
            # Patch only our changed modules. Any concurrent graph/annotation
            # publication advances the version beyond our own write count and
            # leaves normal cross-process invalidation in charge instead.
            nodes = tuple(
                replace(
                    node,
                    data=_annotation_cache_data(
                        node, updated[node.id], graph.snapshot.files[node.path]
                    ),
                )
                if node.id in updated
                else node
                for node in graph.nodes
            )
            app._graph_cache = (after, replace(graph, nodes=nodes))
    else:
        if app._rebuild_request(graph, request["kind"], request)["request_id"] != request_id:
            raise ValueError("Analysis inputs changed; prepare a new review request")
        app.store.save_review(
            str(app.root),
            graph.snapshot.id,
            request_id,
            request["kind"],
            {
                "basis": "agent-reported-proposal",
                "scope": request["scope"],
                "request_options": request.get("request_options", {}),
                **_result_metadata(request),
                "children": request.get("children", []),
                "input_identity": input_identity(request),
                "resource_context": review_context(app, graph, request["scope"]),
                "result": result,
            },
        )
    return {
        "request_id": request_id,
        "kind": request["kind"],
        "saved": True,
        "snapshot_id": graph.snapshot.id,
        "validation": "Shape, declared coverage and evidence references checked; semantic quality remains reviewable.",
    }


def reviews(
    app: Application, kind: str | None = None, limit: int = 12, offset: int = 0
) -> dict[str, Any]:
    _bounds(limit, offset=offset)
    saved = [row for row in app.store.reviews(str(app.root), kind, include_requests=False)]
    if not saved:
        return {"items": [], "total": 0}
    graph = app._live()
    rows = [app._review_state(row, graph) for row in saved[offset : offset + limit]]
    # Results are explicit expansions; keep default lists concise.
    return {
        "items": [
            {key: value for key, value in row.items() if key != "result"}
            | {
                "producer": row["result"].get("result", {}).get("producer"),
                "summary": review_summary(row),
            }
            for row in rows
        ],
        "total": len(saved),
        "offset": offset,
        "next_offset": offset + len(rows) if offset + len(rows) < len(saved) else None,
        "opportunities": actionable_opportunities(rows),
        "scout_findings": scout_proposals(
            rows, {node.id for node in graph.nodes if node.kind == "module"}, 5
        ),
    }


def _review_state(app: Application, row: dict[str, Any], graph: Graph) -> dict[str, Any]:
    if row["result"].get("scope") is None:
        return row | {
            "status": "unknown" if row["snapshot_id"] == graph.snapshot.id else "stale",
            "freshness_reason": "Analysis input identity not recorded",
        }
    current = applicable_request(app, row, graph) is not None
    return row | {
        "status": "current" if current else "stale",
        "checked_snapshot_id": graph.snapshot.id,
        "freshness_reason": "Inputs match" if current else "Inputs or validation changed",
    }


def _rebuild_request(
    app: Application, graph: Graph, kind: str, body: dict[str, Any]
) -> dict[str, Any]:
    options = dict(body.get("request_options", {}))
    if body.get("children"):
        children = []
        for child in body["children"]:
            result = app.store.get_review(str(app.root), child["request_id"], "fresh-eyes")
            request = applicable_request(app, result, graph) if result else None
            if request is None or result is None:
                raise ValueError("A product review's independent input is missing or stale")
            children.append({"request": request, "result": result["result"]["result"]})
        return build_product_request(
            graph,
            children,
            **{k: options[k] for k in ("max_packet_bytes", "max_result_bytes") if k in options},
        )
    options.update(_request_context(app, graph, kind, options))
    request = build_request(graph, kind, body["scope"], **options)
    request["pattern_mode"] = body.get("pattern_mode")
    return request


def analysis_product_request(app: Application, review_ids: list[str]) -> dict[str, Any]:
    if not isinstance(review_ids, list) or not 1 <= len(review_ids) <= 16:
        raise ValueError("Product synthesis needs one to sixteen independent review IDs")
    graph = app._current()
    app._require_live(graph)
    request = app._rebuild_request(
        graph, "fresh-eyes", {"children": [{"request_id": identity} for identity in review_ids]}
    )
    app.store.save_review(
        str(app.root), graph.snapshot.id, request["request_id"], "request", request
    )
    return {k: request[k] for k in ("request_id", "kind", "packet", "response_schema")}


def analysis_saved(
    app: Application, request_id: str, pipeline: dict[str, Any] | None = None
) -> bool:
    request = app.store.get_review(str(app.root), request_id, "request")
    if request is None:
        return False
    graph = app._current()
    if request["result"]["kind"] == "annotate":
        targets = set(request["result"]["scope"])
        current = [node for node in graph.nodes if node.id in targets]
        return len(current) == len(targets) and all(
            node.data.get("annotation_status") == "current"
            and pipeline_matches(node.data["annotation"], pipeline)
            for node in current
        )
    row = app.store.get_review(str(app.root), request_id, request["result"]["kind"])
    if row is None or app._review_state(row, graph)["status"] != "current":
        return reuse_analysis(app, request["result"], pipeline)
    if not pipeline_matches(row["result"]["result"], pipeline):
        return False
    retain_review(app, graph, row, request["result"])
    return True


def analysis_selection(app: Application, request_id: str) -> list[dict[str, Any]]:
    graph = app._current()
    app._require_live(graph)
    row = app.store.get_review(str(app.root), request_id, "pattern-screen")
    if row is None or app._review_state(row, graph)["status"] != "current":
        raise ValueError("Pattern screening is missing or stale; run screening again")
    return [
        {"module_id": item["module_id"], "pattern_ids": list(item["pattern_ids"])}
        for item in row["result"]["result"]["selections"]
    ]


def _reconciliation_rows(app, graph: Graph, kind: str, review_ids: list[str]) -> list[dict]:
    rows = []
    allowed = ("patterns", "pattern-screen", "architecture") if kind == "patterns" else (kind,)
    for identity in review_ids:
        candidates = app.store.reviews(str(app.root), review_id=identity, include_requests=False)
        row = next((item for item in candidates if item["kind"] in allowed), None)
        if row is None or app._review_state(row, graph)["status"] != "current":
            raise ValueError("Reconciliation found a missing or stale review batch")
        rows.append(row)
    return rows


def _pattern_reconciliation(selected: set[str], rows: list[dict]) -> dict:
    scouts = [row for row in rows if row["kind"] == "architecture"]
    reviewed = reconcile_patterns(
        selected,
        [row for row in rows if row["kind"] != "architecture"],
        {card["id"] for card in catalogue()},
    )
    if scouts:
        reviewed["architecture"] = reconcile_architecture(selected, scouts)
    return reviewed


def analysis_reconcile(
    app, kind: str, review_ids: list[str], scope: list[str] | None = None
) -> dict[str, Any]:
    graph = app._current()
    if kind == "delivery":
        return {
            "status": "completed",
            "phase": "Final reconciliation",
            "declared_modules": 0,
            "review_ids": review_ids,
        } | reconcile_delivery(_reconciliation_rows(app, graph, kind, review_ids))
    selected = {
        n.id
        for n in graph.nodes
        if n.kind == "module" and (scope is None or n.id in scope or n.path in scope)
    }
    if kind in {"patterns", "architecture"} and not scope:
        selected = {node.id for node in production_modules(graph)}
    base = {
        "status": "completed",
        "phase": "Final reconciliation",
        "declared_modules": len(selected),
        "review_ids": review_ids,
    }
    if kind in {"mapping", "annotate"}:
        current = {
            n.id
            for n in graph.nodes
            if n.kind == "module" and n.data.get("annotation_status") == "current"
        }
        if selected - current:
            raise ValueError("Mapping reconciliation found missing or stale module understanding")
        return base | {"annotated_modules": len(selected)}
    rows = _reconciliation_rows(app, graph, kind, review_ids)
    if kind == "architecture":
        return base | reconcile_architecture(selected, rows)
    if kind == "patterns":
        return (
            base
            | _pattern_reconciliation(selected, rows)
            | {
                "excluded_modules": excluded_modules(graph) if not scope else [],
            }
        )
    products = [
        row
        for row in rows
        if row["result"].get("children") and set(row["result"]["scope"]) == selected
    ]
    if not products:
        raise ValueError(
            "Fresh Eyes reconciliation needs a product review covering every subsystem"
        )
    return base | {
        "alternatives": 3,
        "product_review_id": products[-1]["id"],
        "blind_design": True,
        "source_comparison": "not_performed",
    }
