"""Bounded structural guidance without pretending static facts prove behavior."""

from __future__ import annotations

import json
import re
from collections import Counter, deque
from dataclasses import asdict
from typing import Any

from anaxigraph.contracts import Graph, Node

from .architecture_guidance import coverage as architecture_coverage
from .architecture_guidance import proposals as architecture_proposals
from .charter import CHARTER, boundary_findings, module_facts


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


def _implementation_owner(node: Node) -> bool:
    return not (
        node.path.endswith((".md", ".rst", ".txt", ".json", ".csv"))
        or node.data.get("parse_status") == "unparsed"
    )


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
                "review_id": row["id"],
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
        "review_id": row["id"],
        "alternatives": [
            {
                "name": _short(item.get("name"), 100),
                "design": _short(item.get("design"), 260),
                "pros": [_short(value, 140) for value in item.get("pros", [])[:1]],
                "cons": [_short(value, 140) for value in item.get("cons", [])[:1]],
            }
            for item in alternatives
        ],
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
    architecture_task = bool(
        re.search(
            r"\b(architectur(?:e|al)|design|refactor(?:ing)?|simplify|modularity|maintainability|patterns?)\b",
            task,
            re.I,
        )
    )
    if architecture_task:
        fresh = next(
            (
                proposal
                for row in current
                if row.get("kind") == "fresh-eyes"
                and (proposal := _fresh_proposal(row)) is not None
            ),
            None,
        )
        if fresh is not None:
            return selected[:1] + [fresh]
    return selected


def _coding_steps(
    graph: Graph,
    candidates: list[Node],
    matches: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    tests: list[str],
    impacted: set[str],
) -> list[dict[str, Any]]:
    if not candidates:
        return [
            {
                "kind": "placement",
                "basis": "insufficient-evidence",
                "suggestion": "No executable owner was found. Inspect entry points and existing responsibilities before adding a new module.",
                "evidence": [],
            }
        ]
    owner = candidates[0]
    facts = module_facts(graph, owner)
    by_id = {node.id: node for node in graph.nodes}
    reusable = next(
        (
            by_id[match["id"]]
            for match in matches
            if match.get("id") in by_id
            and by_id[match["id"]].path == owner.path
            and by_id[match["id"]].kind in {"function", "class", "interface"}
            and not by_id[match["id"]].name.startswith("_")
        ),
        None,
    )
    reuse = (
        f" Check whether {reusable.name} already supplies the requested behavior."
        if reusable
        else " Check its existing interface before adding a sibling implementation."
    )
    steps = [
        {
            "kind": "placement-and-reuse",
            "basis": "candidate-owner-and-source-observations",
            "owner": owner.id,
            "suggestion": _short(
                f"Start by inspecting {owner.path}.{reuse} Keep implementation details behind a small public contract; extract a boundary when a responsibility changes independently.",
                500,
            ),
            "evidence": [{"path": owner.path, "line": reusable.line if reusable else owner.line}],
            "public_interface": {
                "basis": facts["public_surface"]["basis"],
                "count": facts["public_surface"]["count"],
                "names": facts["public_surface"]["names"][:6],
            },
        }
    ]
    if contracts:
        contract = next((item for item in contracts if item["owner"] == owner.id), contracts[0])
        verb = (
            "Check and preserve the recorded obligation:"
            if contract["annotation_status"] == "current"
            else "Verify the recorded obligation before relying on it:"
        )
        steps.append(
            {
                "kind": "contract",
                "basis": contract.get("basis", "inferred"),
                "owner": contract["owner"],
                "suggestion": _short(f"{verb} {contract['statement']}", 360),
                "annotation_status": contract["annotation_status"],
                "evidence": contract.get("evidence", [])[:2],
            }
        )
    consumers = sorted(impacted - {node.path for node in candidates} - set(tests))[:3]
    steps.append(
        {
            "kind": "compatibility-and-tests",
            "basis": "heuristic-backed-by-observed-consumers",
            "suggestion": "Start with local behavior and public-contract checks. Verify consumers when behavior, contracts or shared data/configuration/resources change. Unchanged signatures alone do not establish compatibility. These observed test links are not a complete CI plan and do not replace required project checks.",
            "consumers": consumers,
            "tests": tests[:4],
            "component_tests": facts["observed_test_paths"][:4],
            "consumer_tests": facts["consumer_test_paths"][:4],
            "evidence": [],
        }
    )
    return steps


def _fit_brief(brief: dict[str, Any]) -> dict[str, Any]:
    """Keep the default expansion small; original source/reviews remain inspectable."""
    proposals = brief["implementation_advice"]["review_proposals"]
    collections = [
        proposals,
        brief["reuse_candidates"],
        brief["contracts"],
        brief["affected_paths"],
        brief["tests"],
        brief["likely_owners"],
        brief["implementation_advice"]["steps"],
    ]
    while len(json.dumps(brief, ensure_ascii=False, indent=2).encode()) > 7000:
        collection = next(
            (
                items
                for items in collections
                if items and (items is not proposals or len(items) > 1)
            ),
            proposals or None,
        )
        if collection is None:
            break
        if collection is brief["contracts"]:
            brief["contract_overflow"] += 1
        collection.pop()
        brief["context_truncated"] = True
    return brief


def _candidates(modules: dict[str, Node], matches: list[dict[str, Any]]) -> list[Node]:
    by_path = {node.path: node for node in modules.values()}
    candidates = list(
        {
            match["path"]: by_path[match["path"]]
            for match in matches
            if match.get("path") in by_path and _implementation_owner(by_path[match["path"]])
        }.values()
    )
    candidates.sort(
        key=lambda node: bool(node.data.get("is_test") or node.path.startswith(("tests/", "test/")))
    )
    return candidates


def _contracts(modules: dict[str, Node], impacted: set[str]) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for node in modules.values():
        if node.path in impacted:
            contracts.extend(
                {
                    "owner": node.id,
                    "annotation_status": node.data.get("annotation_status", "missing"),
                    **contract,
                }
                for contract in node.data.get("annotation", {}).get("contracts", [])
            )
    return contracts


def _reuse_briefs(matches: list[dict[str, Any]], paths: set[str]) -> list[dict[str, Any]]:
    return [
        {
            key: _short(match[key], 220) if key in {"name", "overview"} else match[key]
            for key in ("id", "name", "path", "overview", "annotation_status")
            if key in match
        }
        for match in matches[:8]
        if match.get("path") in paths
    ]


def _contract_brief(contract: dict[str, Any], graph: Graph) -> dict[str, Any]:
    statement = _short(contract.get("statement"), 360)
    return {
        key: contract[key] for key in ("owner", "basis", "annotation_status") if key in contract
    } | {
        "statement": statement,
        "statement_truncated": contract.get("statement") != statement,
        "evidence": _references(contract.get("evidence"), graph),
    }


def prepare(
    graph: Graph,
    matches: list[dict[str, Any]],
    task: str,
    reviews: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    modules = {node.id: node for node in graph.nodes if node.kind == "module"}
    candidates = _candidates(modules, matches)
    paths = {node.path for node in candidates}
    impacted = affected_paths(graph, paths)
    contracts = _contracts(modules, impacted)
    tests = [
        node.path
        for node in modules.values()
        if node.path in impacted and (node.data.get("is_test") or node.path.startswith("tests/"))
    ]
    owner_ids = {node.id for node in candidates}
    saved_reviews = reviews or []
    proposals = _review_proposals(graph, saved_reviews, owner_ids, task)
    brief: dict[str, Any] = {
        "task": _short(task, 1200),
        "snapshot_id": graph.snapshot.id,
        "likely_owners": [
            {
                "id": n.id,
                "path": n.path,
                "overview": _short(n.data.get("annotation", {}).get("overview"), 180),
                "annotation_status": n.data.get("annotation_status", "missing"),
            }
            for n in candidates
        ],
        "reuse_candidates": _reuse_briefs(matches, paths),
        "contracts": [_contract_brief(contract, graph) for contract in contracts[:6]],
        "contract_overflow": max(0, len(contracts) - 6),
        "affected_paths": sorted(impacted)[:50],
        "affected_total": len(impacted),
        "tests": sorted(set(tests))[:20],
        "tests_total": len(set(tests)),
        "implementation_advice": {
            "principles": [{"id": item["id"], "principle": item["principle"]} for item in CHARTER],
            "architecture_review": architecture_coverage(saved_reviews, owner_ids),
            "steps": _coding_steps(graph, candidates, matches, contracts, tests, impacted),
            "review_proposals": proposals,
            "review_scope": "At most two owner-scoped proposals; expand saved reviews for other options. A proposal is not an instruction to rewrite.",
        },
        "uncertainty": [
            "Placement is ranked evidence, not accepted architectural intent.",
            "The impact neighborhood is bounded to two reference levels; dynamic references, shared resources and unparsed languages can hide consumers. It is not a complete CI scope.",
            "Tests are associated by observed references; no tests were executed.",
        ],
    }
    if not proposals:
        brief["uncertainty"].append("No current owner-scoped review supports a pattern change.")
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
        if node.data.get("loc", 0) > 500:
            findings.append(
                {
                    "kind": "large-module",
                    "entity": node.id,
                    "measured": node.data["loc"],
                    "threshold": 500,
                    "status": "concern",
                    "message": "Inspect responsibility boundaries; size alone is not a defect.",
                }
            )
        if incoming[node.id] > 15:
            findings.append(
                {
                    "kind": "high-fan-in",
                    "entity": node.id,
                    "measured": incoming[node.id],
                    "threshold": 15,
                    "status": "concern",
                    "message": "Check public contract stability before changing this shared dependency.",
                }
            )
    return findings + boundary_findings(graph)
