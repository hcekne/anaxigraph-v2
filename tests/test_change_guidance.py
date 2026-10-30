"""Task briefs turn saved evidence into restrained, actionable coding advice."""

from __future__ import annotations

import copy
import json
from dataclasses import replace

from anaxigraph.analysis.charter import CHARTER
from anaxigraph.analysis.guidance import prepare
from anaxigraph.contracts import Edge, Graph, Node, Snapshot


def graph_and_matches():
    billing = Node(
        "module:billing.py",
        "module",
        "billing",
        "billing.py",
        data={
            "annotation_status": "current",
            "annotation": {
                "overview": "Retrieve invoices for an authorized tenant.",
                "contracts": [
                    {
                        "statement": "Preserve tenant isolation.",
                        "basis": "declared",
                        "evidence": [{"path": "billing.py", "line": 1}],
                    }
                ],
            },
        },
    )
    function = Node(
        "function:billing.py:list_invoices", "function", "list_invoices", "billing.py", 20
    )
    caller = Node("module:api/export.py", "module", "export", "api/export.py")
    tests = Node(
        "module:tests/test_billing.py",
        "module",
        "test_billing",
        "tests/test_billing.py",
        data={"is_test": True},
    )
    docs = Node("module:docs/start.md", "module", "start", "docs/start.md")
    snapshot = Snapshot(
        "snapshot-1",
        "/fixture",
        "revision-1",
        False,
        {n.path: "a" * 64 for n in (billing, caller, tests, docs)},
        "now",
    )
    graph = Graph(
        snapshot,
        (billing, function, caller, tests, docs),
        (
            Edge(caller.id, function.id, "calls", caller.path),
            Edge(tests.id, function.id, "calls", tests.path),
        ),
    )
    matches = [
        {
            "id": function.id,
            "name": function.name,
            "path": function.path,
            "overview": "Retrieve existing invoices.",
        }
    ]
    return graph, matches


def pattern_review(graph, **evaluation):
    item = {
        "module_id": "module:billing.py",
        "pattern_id": "adapter",
        "opportunity": 2,
        "current_fit": "partial",
        "proposed_change": "Contain export-specific serialization behind the existing billing interface.",
        "pros": ["Preserves the existing billing owner."],
        "cons": ["Adds an interface that may not be needed for one format."],
        "alternative": "Keep one local formatter until another format is needed.",
        "evidence": [{"path": "billing.py", "line": 20}],
    }
    item.update(evaluation)
    return {
        "id": "pattern-review",
        "kind": "patterns",
        "status": "current",
        "snapshot_id": graph.snapshot.id,
        "result": {"scope": ["module:billing.py"], "result": {"evaluations": [item]}},
    }


def test_existing_behavior_contract_consumers_and_tests_guide_the_change():
    graph, matches = graph_and_matches()
    brief = prepare(graph, matches, "Add CSV export of invoices")
    steps = brief["implementation_advice"]["steps"]
    reuse = next(step for step in steps if step["kind"] == "placement-and-reuse")
    assert reuse["owner"] == "module:billing.py"
    assert "list_invoices" in reuse["suggestion"]
    assert reuse["evidence"] == [{"path": "billing.py", "line": 20}]
    contract = brief["contracts"][0]
    assert "tenant isolation" in contract["statement"]
    checks = next(step for step in steps if step["kind"] == "compatibility-and-tests")
    assert checks["consumers"] == ["api/export.py"]
    assert checks["tests"] == ["tests/test_billing.py"]
    assert "no tests were executed" in " ".join(brief["uncertainty"])


def test_documentation_is_not_an_implementation_starting_point():
    graph, _ = graph_and_matches()
    brief = prepare(graph, [{"id": "module:docs/start.md", "path": "docs/start.md"}], "Add export")
    assert brief["likely_owners"] == []
    advice = brief["implementation_advice"]
    assert advice["steps"][0]["basis"] == "insufficient-evidence"
    assert advice["review_proposals"] == []
    assert not any(step.get("owner") == "module:docs/start.md" for step in advice["steps"])


def test_current_positive_review_is_a_proposal_with_costs_and_a_simpler_option():
    graph, matches = graph_and_matches()
    brief = prepare(graph, matches, "Add export", [pattern_review(graph)])
    proposal = brief["implementation_advice"]["review_proposals"][0]
    assert proposal["status"] == "proposal-needs-source-verification"
    assert proposal["pros"] and proposal["cons"] and proposal["simpler_alternative"]
    assert proposal["expand"] == "review:pattern-review"
    assert proposal["evidence"] == [{"path": "billing.py", "line": 20}]


def test_stale_unrelated_unknown_zero_and_ungrounded_patterns_do_not_become_advice():
    graph, matches = graph_and_matches()
    stale = pattern_review(graph) | {"status": "stale"}
    old_snapshot = pattern_review(graph) | {"snapshot_id": "old"}
    wrong_scope = pattern_review(graph, module_id="module:api/export.py")
    reviews = [
        stale,
        old_snapshot,
        wrong_scope,
        pattern_review(graph, opportunity=None),
        pattern_review(graph, opportunity=0),
        pattern_review(graph, evidence=[]),
    ]
    assert (
        prepare(graph, matches, "Add export", reviews)["implementation_advice"]["review_proposals"]
        == []
    )


def test_fresh_eyes_retains_three_options_for_design_work_without_a_winner():
    graph, matches = graph_and_matches()
    review = {
        "id": "blind-review",
        "kind": "fresh-eyes",
        "status": "current",
        "snapshot_id": graph.snapshot.id,
        "result": {
            "scope": ["module:billing.py"],
            "result": {
                "alternatives": [
                    {
                        "name": name,
                        "design": f"Deliver behavior using {name}.",
                        "pros": ["Clear ownership."],
                        "cons": ["Operational tradeoff."],
                    }
                    for name in ("Local process", "Queued processing", "Declarative rules")
                ]
            },
        },
    }
    proposal = prepare(graph, matches, "Refactor invoice architecture", [review])[
        "implementation_advice"
    ]["review_proposals"][0]
    assert len(proposal["alternatives"]) == 3
    assert "winner" not in proposal
    assert proposal["status"] == "three-unverified-design-options"
    mixed = prepare(
        graph, matches, "Assess architectural options", [pattern_review(graph), review]
    )["implementation_advice"]["review_proposals"]
    assert {item["kind"] for item in mixed} == {"pattern", "fresh-eyes"}
    assert len(next(item for item in mixed if item["kind"] == "fresh-eyes")["alternatives"]) == 3
    assert (
        prepare(graph, matches, "Fix invoice rounding", [review])["implementation_advice"][
            "review_proposals"
        ]
        == []
    )


def test_stale_contracts_require_verification_and_input_is_unchanged():
    graph, matches = graph_and_matches()
    data = copy.deepcopy(graph.nodes[0].data)
    data["annotation_status"] = "stale"
    changed = replace(graph, nodes=(replace(graph.nodes[0], data=data), *graph.nodes[1:]))
    before = copy.deepcopy(changed)
    brief = prepare(changed, matches, "Add export")
    contract = brief["contracts"][0]
    assert contract["annotation_status"] == "stale"
    assert "verify stale" in brief["implementation_advice"]["steps"][0]["suggestion"]
    assert changed == before


def test_large_saved_descriptions_produce_a_bounded_progressive_brief():
    graph, matches = graph_and_matches()
    data = copy.deepcopy(graph.nodes[0].data)
    data["annotation"]["overview"] = "overview " * 1000
    data["annotation"]["contracts"] *= 30
    graph = replace(graph, nodes=(replace(graph.nodes[0], data=data), *graph.nodes[1:]))
    matches *= 20
    review = pattern_review(
        graph, pros=["possible benefit " * 100] * 10, cons=["possible cost " * 100] * 10
    )
    brief = prepare(graph, matches, "Assess architecture " * 200, [review])
    assert len(json.dumps(brief, ensure_ascii=False).encode()) <= 7000
    assert len(json.dumps(brief, ensure_ascii=False, indent=2).encode()) <= 7000
    assert brief["contract_overflow"] > 0
    assert brief["task_truncated"]
    principles = {item["id"]: item["guidance"] for item in CHARTER}
    steps = {item["kind"]: item for item in brief["implementation_advice"]["steps"]}
    placement = steps["placement-and-reuse"]["suggestion"]
    checks = steps["compatibility-and-tests"]["suggestion"]
    assert principles["ownership"] in placement
    assert principles["change-locality"] in placement
    assert principles["component-tests"] in checks
    assert principles["effects"] in checks
    assert "independent deployment" in checks and "required project checks" in checks
    assert "auth/schema/runtime/build" in checks and "runtime registrations" in checks
    assert "principles" not in brief["implementation_advice"]


def test_executable_owners_are_preferred_to_test_matches():
    graph, matches = graph_and_matches()
    tests = {"id": "module:tests/test_billing.py", "path": "tests/test_billing.py"}
    brief = prepare(graph, [tests, *matches], "Add invoice export")
    assert brief["likely_owners"][0]["path"] == "billing.py"
    assert brief["implementation_advice"]["steps"][0]["owner"] == "module:billing.py"
