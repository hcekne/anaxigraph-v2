"""Bounded structural guidance without pretending static facts prove behavior."""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import asdict
from typing import Any

from anaxigraph.contracts import SOURCE_SUFFIXES, Graph, Node

from .architecture_guidance import brief_proposals as architecture_proposals
from .architecture_guidance import coverage as architecture_coverage
from .architecture_guidance import pattern_proposals
from .charter import CHARTER, module_facts, relations
from .charter import structural_findings as structural_findings
from .fields import references as _references
from .fields import short as _short


def module_for(graph: Graph, value: str) -> Node | None:
    return next(
        (node for node in graph.nodes if node.kind == "module" and value in (node.id, node.path)),
        None,
    )


def _module_edges(
    graph: Graph, entity: Node, outgoing: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Roll up observed cross-file references, retaining each original witness."""
    owners = {n.id: n.path for n in graph.nodes}
    modules = {n.path: n.id for n in graph.nodes if n.kind == "module"}
    projected = {}
    for edge in graph.edges:
        source, target = owners.get(edge.source), owners.get(edge.target)
        if source == target or source not in modules or target not in modules:
            continue
        if entity.path not in {source, target}:
            continue
        key = (source, target, edge.kind, edge.resolution)
        if key not in projected:
            projected[key] = {
                **asdict(edge),
                "source": modules[source],
                "target": modules[target],
                "projected_from": asdict(edge),
                "observations": 0,
            }
        projected[key]["observations"] += 1
    incoming = [e for e in projected.values() if e["target"] == entity.id]
    local = [
        e
        for e in outgoing
        if owners.get(e["target"]) not in modules or owners.get(e["target"]) == entity.path
    ]
    outgoing = [e for e in projected.values() if e["source"] == entity.id] + local
    return incoming, outgoing


def _compact_node(node: Node) -> dict[str, Any]:
    data = {
        key: node.data[key]
        for key in ("loc", "annotation_status", "parse_status", "behavioral_validation")
        if key in node.data
    }
    annotation = node.data.get("annotation")
    if annotation:
        data["annotation"] = {"overview": _short(annotation.get("overview"), 240)}
    return asdict(Node(node.id, node.kind, node.name, node.path, node.line, data))


def _entity_relations(graph: Graph, entity: Node) -> tuple[list[dict], list[dict]]:
    incoming = [asdict(e) for e in graph.edges if e.target == entity.id and e.kind != "contains"]
    outgoing = [asdict(e) for e in graph.edges if e.source == entity.id]
    if entity.kind == "module":
        return _module_edges(graph, entity, outgoing)
    return incoming, outgoing


def _relationship_page(
    graph: Graph, entity: Node, limit: int, offset: int, links: tuple[list[dict], list[dict]]
) -> dict[str, Any]:
    incoming, outgoing = links
    page = [("incoming", edge) for edge in incoming] + [("outgoing", edge) for edge in outgoing]
    selected = page[offset : offset + limit]
    related = {e["source"] for _, e in selected} | {e["target"] for _, e in selected}
    next_offset = offset + len(selected)
    return {
        "incoming": [edge for direction, edge in selected if direction == "incoming"],
        "outgoing": [edge for direction, edge in selected if direction == "outgoing"],
        "neighbors": [
            _compact_node(n) for n in graph.nodes if n.id != entity.id and n.id in related
        ],
        "incoming_total": len(incoming),
        "outgoing_total": len(outgoing),
        "offset": offset,
        "next_offset": next_offset if next_offset < len(page) else None,
        "truncated": offset > 0 or next_offset < len(page),
    }


def neighborhood(
    graph: Graph, entity_id: str, limit: int = 30, offset: int = 0, full: bool = False
) -> dict[str, Any]:
    entity = next((n for n in graph.nodes if entity_id == n.id), None) or module_for(
        graph, entity_id
    )
    if entity is None:
        raise ValueError("Entity not found; use modules to discover valid entity IDs")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    links = _entity_relations(graph, entity)
    result = _relationship_page(graph, entity, limit, offset, links) | {
        "entity": asdict(entity) if full else _compact_node(entity),
        "detail": "full" if full else "compact",
        "expand": {"entity_id": entity.id, "full": True},
        "projection": "module rollup of observed symbol references"
        if entity.kind == "module"
        else "source observations",
    }
    if not full:
        contracts = _contracts(graph, {entity.path}, {entity.id})
        result["contracts"] = contracts[:3]
        result["contract_overflow"] = max(0, len(contracts) - 3)
        while limit > 1 and len(json.dumps(result, ensure_ascii=False, indent=2).encode()) > 8000:
            limit = max(1, limit // 2)
            result.update(_relationship_page(graph, entity, limit, offset, links))
    return result


def affected_paths(graph: Graph, paths: set[str], depth: int = 2) -> set[str]:
    consumers = relations(graph)[1]
    pending = deque((path, 0) for path in paths)
    found = set(paths)
    while pending:
        path, level = pending.popleft()
        if level < depth:
            for caller in consumers[path]["consumers"] if path in consumers else ():
                if caller not in found:
                    found.add(caller)
                    pending.append((caller, level + 1))
    return found


def _fresh_proposal(row: dict[str, Any]) -> dict[str, Any] | None:
    alternatives = row.get("result", {}).get("result", {}).get("alternatives", [])
    if len(alternatives) != 3:
        return None
    return {
        "kind": "fresh-eyes",
        "status": "three-unverified-design-options",
        "alternatives": [{"name": _short(item.get("name"), 100)} for item in alternatives],
        "expand": "review:" + row["id"],
    }


def _review_proposals(
    graph: Graph, reviews: list[dict[str, Any]], owners: set[str], task: str
) -> list[dict[str, Any]]:
    current = [
        row
        for row in reviews
        if row.get("status") == "current"
        and row.get("snapshot_id") == graph.snapshot.id
        and owners.intersection(row.get("result", {}).get("scope", []))
    ]
    selected = sorted(
        architecture_proposals(current, owners) + pattern_proposals(current, owners, graph),
        key=lambda item: (-item["opportunity"], item["proposal_id"]),
    )[:2]
    if not re.search(
        r"\b(architectur\w*|design|refactor\w*|simplify|modularity|maintainability|patterns?)\b",
        task,
        re.I,
    ):
        return selected
    fresh = next(
        (
            proposal
            for row in current
            if row.get("kind") == "fresh-eyes" and (proposal := _fresh_proposal(row)) is not None
        ),
        None,
    )
    return (selected + ([fresh] if fresh is not None else []))[:2]


def _coding_steps(
    graph: Graph, candidates: list[Node], matches: list[dict], tests: list[str], impacted: set[str]
) -> list[dict]:
    principles = {item["id"]: item["guidance"] for item in CHARTER}
    if not candidates:
        return [
            {
                "kind": "placement",
                "basis": "insufficient-evidence",
                "suggestion": principles["ownership"],
                "evidence": [],
            }
        ]
    owner = candidates[0]
    facts = module_facts(graph, owner)
    match = next((item for item in matches if item.get("path") == owner.path), {})
    reusable = next((node for node in graph.nodes if node.id == match.get("id")), owner)
    return [
        {
            "kind": "placement-and-reuse",
            "basis": "candidate-owner-and-source-observations",
            "owner": owner.id,
            "suggestion": f"Inspect {owner.path} and {match.get('matched_symbol', reusable.name)} for reuse; verify stale obligations. {principles['ownership']} {principles['change-locality']}",
            "evidence": match.get("evidence", [{"path": owner.path, "line": reusable.line}])[:1],
        },
        {
            "kind": "compatibility-and-tests",
            "basis": "heuristic-backed-by-observed-consumers",
            "suggestion": principles["component-tests"] + " " + principles["effects"],
            "consumers": sorted(impacted - {node.path for node in candidates} - set(tests))[:3],
            "tests": tests[:4],
            "component_tests": facts["observed_test_paths"][:4],
            "consumer_tests": facts["consumer_test_paths"][:4],
            "evidence": [],
        },
    ]


def _fit_brief(brief: dict[str, Any]) -> dict[str, Any]:
    """Treat size as a prose budget, retaining ranked owners and contract evidence."""
    if len(json.dumps(brief, ensure_ascii=False, indent=2).encode()) <= 3400:
        return brief
    brief["context_truncated"] = True
    for owner in brief["likely_owners"]:
        owner["overview"] = _short(owner["overview"], 80)
    for contract in brief["contracts"]:
        contract["statement"] = _short(contract["statement"], 180)
        contract["statement_truncated"] |= contract["statement"].endswith("...")
    brief["affected_paths"] = brief["affected_paths"][:3]
    brief["tests"] = brief["tests"][:3]
    return brief


def _candidates(modules: dict[str, Node], matches: list[dict[str, Any]]) -> list[Node]:
    by_path = {node.path: node for node in modules.values()}
    paths = dict.fromkeys(match.get("path") for match in matches)
    explicit = {m["path"] for m in matches if "exact_path" in m.get("match_scores", {})}
    candidates = [
        by_path[path]
        for path in paths
        if path in by_path
        and by_path[path].data.get("parse_status", "parsed") == "parsed"
        and path.endswith(SOURCE_SUFFIXES)
        and (not by_path[path].data.get("is_test") or path in explicit)
    ]
    candidates.sort(key=lambda n: bool(n.data.get("is_test")) and n.path not in explicit)
    return candidates[:3]


def _contracts(graph: Graph, impacted: set[str], owners: set[str]) -> list[dict[str, Any]]:
    contracts = [
        {
            "owner": node.id,
            "annotation_status": node.data.get("annotation_status", "missing"),
            "basis": contract.get("basis", "inferred"),
            "statement": (statement := _short(contract.get("statement"), 360)),
            "statement_truncated": contract.get("statement") != statement,
            "evidence": _references(contract.get("evidence"), graph),
            "index": index,
        }
        for node in graph.nodes
        if node.kind == "module" and node.path in impacted
        for index, contract in enumerate(node.data.get("annotation", {}).get("contracts", []))
    ]
    contracts.sort(key=lambda item: (item["index"] > 0, item["owner"] not in owners, item["index"]))
    for contract in contracts:
        del contract["index"]
    return contracts


def prepare(
    graph: Graph,
    matches: list[dict[str, Any]],
    task: str,
    reviews: list[dict[str, Any]] | None = None,
    delivery_advice: dict[str, Any] | None = None,
) -> dict[str, Any]:
    modules = {node.id: node for node in graph.nodes if node.kind == "module"}
    candidates = _candidates(modules, matches)
    paths = {node.path for node in candidates}
    impacted = affected_paths(graph, paths)
    owner_ids = {node.id for node in candidates}
    contracts = _contracts(graph, impacted, owner_ids)
    tests = [
        node.path
        for node in modules.values()
        if node.path in impacted and node.data.get("is_test", node.path.startswith("tests/"))
    ]
    stems = {node.name.rsplit(".", 1)[-1] for node in candidates}
    tests.sort(key=lambda path: -sum(stem in path for stem in stems))
    saved_reviews = reviews or []
    proposals = _review_proposals(graph, saved_reviews, owner_ids, task)
    by_path = {match["path"]: match for match in reversed(matches)}
    brief: dict[str, Any] = {
        "task": _short(task, 320),
        "snapshot_id": graph.snapshot.id,
        "delivery": delivery_advice,
        "likely_owners": [
            {
                "id": n.id,
                "path": n.path,
                "overview": _short(n.data.get("annotation", {}).get("overview"), 180),
                "annotation_status": n.data.get("annotation_status", "missing"),
                **{
                    key: value
                    for key, value in by_path[n.path].items()
                    if key in {"score", "match_reasons", "evidence"}
                },
            }
            for n in candidates[:3]
        ],
        "contracts": contracts[:3],
        "contract_overflow": max(0, len(contracts) - 3),
        "affected_paths": sorted(impacted)[:8],
        "affected_total": len(impacted),
        "tests": tests[:6],
        "tests_total": len(set(tests)),
        "implementation_advice": {
            "principles_ref": "charter",
            "architecture_review": architecture_coverage(saved_reviews, owner_ids),
            "steps": _coding_steps(graph, candidates, matches, tests, impacted),
            "review_proposals": proposals,
        },
        "uncertainty": [
            "Observed links cover two reference levels, not complete CI scope; no tests were executed.",
        ],
    }
    if brief["task"] != task:
        brief["task_truncated"] = True
    return _fit_brief(brief)
