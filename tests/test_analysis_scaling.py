"""Large review scopes stay complete through bounded independently saved batches."""

from __future__ import annotations

import copy
import json

import pytest

from anaxigraph.analysis import build_product_request, build_request, plan_scopes, validate_result
from anaxigraph.analysis.packets import catalogue
from anaxigraph.contracts import Graph, Node, Snapshot


def graph_of(count: int) -> Graph:
    nodes = tuple(
        Node(
            f"module:feature_{i}.py",
            "module",
            f"feature_{i}",
            f"feature_{i}.py",
            1,
            {
                "loc": 2,
                "annotation_status": "current",
                "annotation": {
                    "overview": "Exposes an observable capability.",
                    "capabilities": [f"Deliver user-visible outcome number {i}."],
                    "contracts": [],
                },
            },
        )
        for i in range(count)
    )
    return Graph(
        Snapshot(
            "snapshot",
            "/example",
            "revision",
            False,
            {node.path: f"fingerprint-{i}" for i, node in enumerate(nodes)},
            "2026-10-25",
        ),
        nodes,
        (),
    )


def fresh_result(request: dict) -> dict:
    return {
        "producer": "test/synthetic",
        "functional_summary": "Deliver the promised user-visible outcomes reliably.",
        "functional_requirements": ["Preserve user-visible outcomes and failure reporting."],
        "functional_coverage": [[item["id"] for item in request["packet"]["requirements"]]],
        "alternatives": [
            {
                "name": f"Approach {i}",
                "design": f"Use independent responsibility arrangement {i}.",
                "requirements": [item["id"] for item in request["packet"]["requirements"]],
                "pros": ["Clear ownership."],
                "cons": ["Needs verification."],
                "diagram": f"flowchart TB\nUser --> Boundary{i}",
            }
            for i in range(3)
        ],
    }


def test_large_repository_fresh_eyes_hierarchy_has_bounded_behavior_only_packets():
    graph = graph_of(640)
    scopes = plan_scopes(graph, "fresh-eyes")
    assert len(scopes) == 40
    assert {item for scope in scopes for item in scope} == {node.id for node in graph.nodes}
    children = []
    for scope in scopes:
        request = build_request(graph, "fresh-eyes", scope)
        result = fresh_result(request)
        validate_result(request, result, graph)
        assert len(request["packet"]["requirements"]) == 16
        children.append({"request": request, "result": result})
    parents = []
    for offset in range(0, len(children), 16):
        request = build_product_request(graph, children[offset : offset + 16])
        result = fresh_result(request)
        validate_result(request, result, graph)
        parents.append({"request": request, "result": result})
    product = build_product_request(graph, parents)
    assert len(product["scope"]) == 640
    assert len(product["packet"]["requirements"]) == 3
    blind = json.dumps(product["packet"])
    assert "feature_" not in blind and "module:" not in blind and "/example" not in blind
    assert len(blind.encode()) < 8000
    validate_result(product, fresh_result(product), graph)


def test_pattern_result_over_old_256kb_limit_retains_all_pairs_and_tradeoffs():
    graph = graph_of(4)
    request = build_request(graph, "patterns", [node.id for node in graph.nodes])
    result = {
        "producer": "test/synthetic",
        "evaluations": [
            {
                "module_id": node.id,
                "pattern_id": card["id"],
                "opportunity": None,
                "current_fit": "unknown",
                "proposed_change": "No change without evidence.",
                "pros": ["Potential responsibility isolation. " * 8],
                "cons": ["Additional coordination and indirection. " * 8],
                "alternative": "Retain the small local implementation.",
                "evidence": [],
            }
            for node in graph.nodes
            for card in catalogue()
        ],
    }
    assert len(result["evaluations"]) == 532
    assert len(json.dumps(result).encode()) > 262_144
    validate_result(request, result, graph)
    short_budget = request | {"max_result_bytes": 262_144}
    with pytest.raises(ValueError, match="configured batch budget"):
        validate_result(short_budget, result, graph)


def test_annotation_planner_only_selects_missing_or_stale_modules():
    graph = graph_of(5)
    nodes = list(graph.nodes)
    nodes[1] = Node(nodes[1].id, "module", nodes[1].name, nodes[1].path, data={})
    nodes[3] = Node(
        nodes[3].id,
        "module",
        nodes[3].name,
        nodes[3].path,
        data={"annotation": {"overview": "old"}, "annotation_status": "stale"},
    )
    graph = Graph(graph.snapshot, tuple(nodes), ())
    assert plan_scopes(graph, "annotate") == [[nodes[1].id], [nodes[3].id]]
    assert len(plan_scopes(graph, "annotate", include_current=True)) == 5
    assert len(plan_scopes(graph, "patterns")) == 5


@pytest.mark.parametrize(
    "symbol,behavior",
    [
        ("InvoiceStore", "Delegate persistence to InvoiceStore."),
        ("create_invoice", "Use create_invoice to accept the request."),
        ("validate", "Use `validate` before accepting input."),
        ("validate", "Call validate() before accepting input."),
    ],
)
def test_blind_requests_reject_literal_symbol_identities(symbol, behavior):
    graph = graph_of(1)
    module = graph.nodes[0]
    module.data["annotation"]["capabilities"] = [behavior]
    graph = Graph(
        graph.snapshot,
        (module, Node("symbol:feature_0.py:test", "function", symbol, module.path)),
        (),
    )
    with pytest.raises(ValueError, match="source identity"):
        build_request(graph, "fresh-eyes")


def test_symbol_boundaries_and_generic_verbs_do_not_reject_behavior():
    graph = graph_of(1)
    module = graph.nodes[0]
    module.data["annotation"]["capabilities"] = ["Create and validate warehouse storage entries."]
    graph = Graph(
        graph.snapshot,
        (
            module,
            Node("symbol:a:Store", "class", "Store", module.path),
            Node("symbol:a:validate", "function", "validate", module.path),
        ),
        (),
    )
    build_request(graph, "fresh-eyes")


def test_pattern_partitions_have_explicit_independent_complete_coverage():
    graph = graph_of(1)
    selected = [card["id"] for card in catalogue()[:10]]
    request = build_request(graph, "patterns", pattern_ids=selected)
    assert [card["id"] for card in request["packet"]["catalogue"]] == selected
    assert request["request_options"]["pattern_ids"] == selected
    assert request["request_id"] != build_request(graph, "patterns")["request_id"]
    with pytest.raises(ValueError, match="catalogue cards"):
        build_request(graph, "patterns", pattern_ids=["missing-card"])


def test_product_synthesis_rejects_missing_briefs_and_incomplete_child_coverage():
    graph = graph_of(1)
    request = build_request(graph, "fresh-eyes")
    result = fresh_result(request)
    incomplete = copy.deepcopy(result)
    incomplete["alternatives"][0]["requirements"] = []
    with pytest.raises(ValueError, match="every requirement"):
        build_product_request(graph, [{"request": request, "result": incomplete}])
    del result["functional_requirements"]
    with pytest.raises(ValueError, match="functional_requirements"):
        build_product_request(graph, [{"request": request, "result": result}])


def test_canonical_results_strip_unknown_source_and_cache_fields_at_every_level():
    from anaxigraph.analysis import canonical_result

    graph = graph_of(1)
    annotation_request = build_request(
        graph, "annotate", sources={graph.nodes[0].path: "value = 1\n"}
    )
    annotation = {
        "node_id": graph.nodes[0].id,
        "overview": "Deliver observable results.",
        "detail": "Keep user-visible outcomes available to callers.",
        "capabilities": ["Deliver observable results."],
        "functions": [],
        "contracts": [
            {
                "statement": "Preserve observable results.",
                "basis": "inferred",
                "evidence": [{"path": graph.nodes[0].path, "line": 1, "raw_source": "PRIVATE"}],
                "raw_source": "PRIVATE",
            }
        ],
        "uncertainty": [],
        "raw_source": "PRIVATE",
    }
    raw = {"producer": "synthetic", "annotations": [annotation], "cache": "PRIVATE"}
    clean = canonical_result(annotation_request, raw)
    validate_result(annotation_request, clean, graph)
    assert "PRIVATE" not in json.dumps(clean)
    assert "PRIVATE" in json.dumps(raw)  # The caller's input was not mutated.

    pattern_request = build_request(graph, "patterns", pattern_ids=[catalogue()[0]["id"]])
    pattern = {
        "producer": "synthetic",
        "raw_source": "PRIVATE",
        "evaluations": [
            {
                "module_id": graph.nodes[0].id,
                "pattern_id": catalogue()[0]["id"],
                "opportunity": 1,
                "current_fit": "absent",
                "proposed_change": "Separate failure ownership.",
                "pros": ["Clear responsibility."],
                "cons": ["Adds coordination."],
                "alternative": "Keep the local operation.",
                "raw_source": "PRIVATE",
                "evidence": [{"path": graph.nodes[0].path, "line": 1, "cache": "PRIVATE"}],
            }
        ],
    }
    clean = canonical_result(pattern_request, pattern)
    validate_result(pattern_request, clean, graph)
    assert "PRIVATE" not in json.dumps(clean)

    fresh_request = build_request(graph, "fresh-eyes")
    fresh = fresh_result(fresh_request)
    fresh["cache"] = "PRIVATE"
    fresh["alternatives"][0]["raw_source"] = "PRIVATE"
    clean = canonical_result(fresh_request, fresh)
    validate_result(fresh_request, clean, graph)
    assert "PRIVATE" not in json.dumps(clean)


def test_canonical_projection_never_repairs_missing_required_fields():
    from anaxigraph.analysis import canonical_result

    graph = graph_of(1)
    request = build_request(graph, "fresh-eyes")
    result = fresh_result(request)
    del result["alternatives"][1]["cons"]
    clean = canonical_result(request, result)
    assert "cons" not in clean["alternatives"][1]
    with pytest.raises(ValueError, match="cons"):
        validate_result(request, clean, graph)


def test_execution_provenance_is_bounded_and_retains_exact_ordered_model_pipeline():
    from anaxigraph.analysis import canonical_result

    graph = graph_of(1)
    request = build_request(graph, "fresh-eyes")
    result = fresh_result(request)
    primary = {"provider": "codex", "model": "small", "cache": "PRIVATE"}
    fallback = {"provider": "codex", "model": "strong", "cache": "PRIVATE"}
    review = {"provider": "claude", "model": "reviewer", "raw_source": "PRIVATE"}
    result["execution"] = {
        "pipeline": {
            "worker": primary | {"fallbacks": [fallback]},
            "review_passes": [review],
            "cache": "PRIVATE",
        },
        "worker": fallback,
        "review_passes": [review],
        "raw_source": "PRIVATE",
    }
    clean = canonical_result(request, result)
    assert clean["execution"] == {
        "pipeline": {
            "worker": {
                "provider": "codex",
                "model": "small",
                "fallbacks": [{"provider": "codex", "model": "strong"}],
            },
            "review_passes": [{"provider": "claude", "model": "reviewer"}],
        },
        "worker": {"provider": "codex", "model": "strong"},
        "review_passes": [{"provider": "claude", "model": "reviewer"}],
    }
    assert "PRIVATE" not in json.dumps(clean)
    oversized = copy.deepcopy(result)
    oversized["execution"]["review_passes"] *= 5
    with pytest.raises(ValueError, match="at most four"):
        canonical_result(request, oversized)


def test_zero_rated_pattern_optional_prose_cannot_bypass_field_bounds():
    graph = graph_of(1)
    request = build_request(graph, "patterns", pattern_ids=[catalogue()[0]["id"]])
    result = {
        "producer": "synthetic",
        "evaluations": [
            {
                "module_id": graph.nodes[0].id,
                "pattern_id": catalogue()[0]["id"],
                "opportunity": 0,
                "pros": ["None."],
                "cons": ["Unnecessary indirection."],
                "alternative": "Keep the local operation.",
                "evidence": [],
                "proposed_change": "x" * 1001,
            }
        ],
    }
    with pytest.raises(ValueError, match="proposed_change"):
        validate_result(request, result, graph)
