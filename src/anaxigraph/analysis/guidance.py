"""Bounded structural guidance without pretending static facts prove behavior."""

from __future__ import annotations

import json
import re
from collections import Counter, deque
from dataclasses import asdict
from typing import Any

from anaxigraph.contracts import Graph, Node

from .architecture_guidance import brief_proposals as architecture_proposals
from .architecture_guidance import coverage as architecture_coverage
from .charter import boundary_findings, module_facts


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


def neighborhood(graph: Graph, entity_id: str, limit: int = 30) -> dict[str, Any]:
    entity = next((n for n in graph.nodes if entity_id == n.id), None) or module_for(
        graph, entity_id
    )
    if entity is None:
        raise ValueError("Entity not found in this checkout's current graph")
    incoming = [asdict(e) for e in graph.edges if e.target == entity.id and e.kind != "contains"]
    outgoing = [asdict(e) for e in graph.edges if e.source == entity.id]
    if entity.kind == "module":
        incoming, outgoing = _module_edges(graph, entity, outgoing)
    related = {e["source"] for e in incoming[:limit]} | {e["target"] for e in outgoing[:limit]}
    neighbors = []
    for node in graph.nodes:
        if node.id in related:
            brief = {
                "loc": node.data.get("loc"),
                "annotation_status": node.data.get("annotation_status"),
            }
            if node.data.get("annotation"):
                brief["overview"] = node.data["annotation"]["overview"]
            neighbors.append(
                asdict(Node(node.id, node.kind, node.name, node.path, node.line, brief))
            )
    neighbors = neighbors[:limit]
    return {
        "entity": asdict(entity),
        "incoming": incoming[:limit],
        "outgoing": outgoing[:limit],
        "neighbors": neighbors,
        "incoming_total": len(incoming),
        "outgoing_total": len(outgoing),
        "truncated": len(incoming) > limit or len(outgoing) > limit,
        "projection": "module rollup of observed symbol references"
        if entity.kind == "module"
        else "source observations",
    }


def affected_paths(graph: Graph, paths: set[str], depth: int = 2) -> set[str]:
    owners = {node.id: node.path for node in graph.nodes}
    reverse: dict[str, set[str]] = {}
    for edge in graph.edges:
        source, target = owners.get(edge.source), owners.get(edge.target)
        if source and target and source != target and edge.resolution == "resolved":
            reverse.setdefault(target, set()).add(source)
    pending = deque((path, 0) for path in paths)
    found = set(paths)
    while pending:
        path, level = pending.popleft()
        if level < depth:
            for caller in reverse.get(path, ()):
                if caller not in found:
                    found.add(caller)
                    pending.append((caller, level + 1))
    return found


def _short(value: Any, maximum: int = 240) -> str:
    text = value if isinstance(value, str) else ""
    raw = text.encode("utf-8")
    return text if len(raw) <= maximum else raw[: maximum - 3].decode("utf-8", "ignore") + "..."


def _references(value: Any, graph: Graph) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {"path": item["path"], "line": item["line"]}
        for item in value
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and item.get("path") in graph.snapshot.files
        and type(item.get("line")) is int
        and item["line"] > 0
    ][:2]


def _pattern_proposals(row: dict[str, Any], owners: set[str], graph: Graph) -> list[dict[str, Any]]:
    result = row.get("result", {}).get("result", {})
    proposals = []
    for item in result.get("evaluations", []):
        score = item.get("opportunity")
        references = _references(item.get("evidence"), graph)
        if (
            item.get("module_id") not in owners
            or type(score) is not int
            or not 1 <= score <= 3
            or not isinstance(item.get("proposed_change"), str)
            or not item["proposed_change"].strip()
            or not references
        ):
            continue
        proposals.append(
            {
                "kind": "pattern",
                "status": "proposal-needs-source-verification",
                "module_id": item["module_id"],
                "pattern_id": item.get("pattern_id"),
                "opportunity": score,
                "current_fit": item.get("current_fit", "unknown"),
                "suggestion": _short(item["proposed_change"], 360),
                "pros": [_short(value, 180) for value in item.get("pros", [])[:2]],
                "cons": [_short(value, 180) for value in item.get("cons", [])[:2]],
                "simpler_alternative": _short(item.get("alternative")),
                "evidence": references,
                "expand": "review:" + row["id"],
            }
        )
    return proposals[:2]


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
    patterns = [
        proposal
        for row in current
        if row.get("kind") == "patterns"
        for proposal in _pattern_proposals(row, owners, graph)
    ][:2]
    selected = (architecture_proposals(current, owners) + patterns)[:2]
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
    if not candidates:
        return [
            {
                "kind": "placement",
                "basis": "insufficient-evidence",
                "suggestion": "Inspect entry points and existing responsibilities before adding a module.",
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
            "suggestion": f"Inspect {owner.path} and {match.get('matched_symbol', reusable.name)} for reuse. Preserve contracts; verify stale obligations. Keep implementation behind the public interface.",
            "evidence": match.get("evidence", [{"path": owner.path, "line": reusable.line}])[:1],
        },
        {
            "kind": "compatibility-and-tests",
            "basis": "heuristic-backed-by-observed-consumers",
            "suggestion": "Start with local behavior and contract checks. Check consumers for changed behavior, contracts or shared inputs. Keep required project checks; source links are not complete CI coverage.",
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
    for step in brief["implementation_advice"]["steps"]:
        step["suggestion"] = _short(step["suggestion"], 180)
    brief["affected_paths"] = brief["affected_paths"][:3]
    brief["tests"] = brief["tests"][:3]
    return brief


def _candidates(modules: dict[str, Node], matches: list[dict[str, Any]]) -> list[Node]:
    by_path = {node.path: node for node in modules.values()}
    paths = dict.fromkeys(match.get("path") for match in matches)
    candidates = [
        by_path[path]
        for path in paths
        if path in by_path
        and by_path[path].data.get("parse_status", "parsed") == "parsed"
        and path.endswith((".py", ".pyi"))
        and not by_path[path].data.get("is_test")
    ]
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
        if node.path in impacted and (node.data.get("is_test") or node.path.startswith("tests/"))
    ]
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
        "tests": sorted(set(tests))[:6],
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


def structural_findings(graph: Graph) -> list[dict[str, Any]]:
    modules = [node for node in graph.nodes if node.kind == "module"]
    owners = {node.id: node.path for node in graph.nodes}
    module_ids = {node.path: node.id for node in modules}
    incoming = Counter(
        target
        for source, target in {
            (owners.get(edge.source), module_ids.get(owners.get(edge.target, "")))
            for edge in graph.edges
            if edge.resolution == "resolved" and owners.get(edge.source) != owners.get(edge.target)
        }
        if source and target
    )
    findings = []
    for node in modules:
        for kind, measured, threshold, message in (
            (
                "large-module",
                node.data.get("loc", 0),
                500,
                "Inspect responsibility boundaries; size alone is not a defect.",
            ),
            (
                "high-fan-in",
                incoming[node.id],
                15,
                "Check public contract stability before changing this shared dependency.",
            ),
        ):
            if measured > threshold:
                findings.append(
                    {
                        "kind": kind,
                        "entity": node.id,
                        "measured": measured,
                        "threshold": threshold,
                        "status": "concern",
                        "message": message,
                    }
                )
    return findings + boundary_findings(graph)
