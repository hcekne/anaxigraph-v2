"""Compact pattern selection and honest reconciliation, independent of persistence."""

from __future__ import annotations

from typing import Any

from anaxigraph.contracts import Graph, Node


def production_modules(graph: Graph) -> list[Node]:
    return [
        node
        for node in graph.nodes
        if node.kind == "module"
        and not node.data.get("is_test")
        and node.data.get("inert_module") is not True
    ]


def excluded_modules(graph: Graph) -> list[dict[str, str]]:
    return [
        {
            "module_id": node.id,
            "path": node.path,
            "reason": "test module" if node.data.get("is_test") else "observed inert marker",
        }
        for node in graph.nodes
        if node.kind == "module"
        and (node.data.get("is_test") or node.data.get("inert_module") is True)
    ]


def _detailed_pairs(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    evaluations = [
        item
        for row in rows
        if row["kind"] == "patterns"
        for item in row["result"]["result"]["evaluations"]
    ]
    pairs = {(item["module_id"], item["pattern_id"]) for item in evaluations}
    if len(pairs) != len(evaluations):
        raise ValueError("Pattern reconciliation found duplicate detailed pairs")
    return pairs


def _screened_selection(selected: set[str], screens: list[dict[str, Any]]) -> set[tuple[str, str]]:
    selections = [item for row in screens for item in row["result"]["result"]["selections"]]
    if len(selections) != len(selected) or {s["module_id"] for s in selections} != selected:
        raise ValueError("Pattern screening must cover every declared module exactly once")
    return {(s["module_id"], card) for s in selections for card in s["pattern_ids"]}


def reconcile_patterns(
    selected: set[str], rows: list[dict[str, Any]], card_ids: set[str]
) -> dict[str, Any]:
    screens = [row for row in rows if row["kind"] == "pattern-screen"]
    pairs = _detailed_pairs(rows)
    expected = {(module, card) for module in selected for card in card_ids}
    if screens:
        expected = _screened_selection(selected, screens)
    if pairs != expected:
        raise ValueError("Pattern reconciliation found incomplete or unexpected detailed coverage")
    return {
        "mode": "empty" if not selected else "screened" if screens else "exhaustive",
        "scope_status": "reviewed" if selected else "no_eligible_modules",
        "screened_pairs": len(selected) * len(card_ids) if screens else 0,
        "selected_pairs": len(expected),
        "detailed_pairs": len(pairs),
        "evaluated_pairs": len(pairs),
        "expected_pairs": len(expected),
        "screening_note": "Unselected pairs are not proven inapplicable; detailed ratings remain reviewable.",
    }


def review_summary(row: dict[str, Any]) -> str:
    result = row["result"].get("result", {})
    if row["kind"] == "architecture":
        return f"{result['lens']} scout; {len(result['assessments'])} modules accounted for; {len(result['findings'])} proposals."
    if row["kind"] == "fresh-eyes":
        return "Three alternatives"
    if row["kind"] == "pattern-screen":
        count = sum(len(item["pattern_ids"]) for item in result["selections"])
        return f"Catalogue screened; {count} selected pairs. Unselected is not proven irrelevant."
    evaluations = result.get("evaluations", [])
    count = sum(
        type(item.get("opportunity")) is int and item["opportunity"] > 0 for item in evaluations
    )
    return f"{len(evaluations)} pairs reviewed; {count} actionable opportunities."


def actionable_opportunities(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    opportunities = [
        {"review_id": row["id"]} | item
        for row in rows
        if row["kind"] == "patterns" and row["status"] == "current"
        for item in row["result"]["result"]["evaluations"]
        if type(item.get("opportunity")) is int and item["opportunity"] > 0
    ]
    opportunities.sort(key=lambda item: -item["opportunity"])
    return [
        {
            "review_id": item["review_id"],
            "module_id": item["module_id"],
            "pattern_id": item["pattern_id"],
            "opportunity": item["opportunity"],
            "change": item.get("proposed_change", "")[:180],
            "evidence": item.get("evidence", [])[:1],
        }
        for item in opportunities[:limit]
    ]
