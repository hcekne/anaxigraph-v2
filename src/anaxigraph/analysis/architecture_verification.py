"""Compare observed boundaries without treating advice or supplied checks as proof."""

from __future__ import annotations

from collections import Counter

from anaxigraph.analysis import extension
from anaxigraph.analysis.changes import changed_paths
from anaxigraph.analysis.charter import _cycles, relations
from anaxigraph.contracts import Graph


def _input_errors(graph: Graph) -> list[str]:
    return [row for row in graph.diagnostics if ".py:" in row or ".pyi:" in row]


def _boundaries(graph: Graph) -> dict:
    modules, raw = relations(graph)
    production = {path for path, node in modules.items() if not node.data.get("is_test")}
    cycles = _cycles({path: raw[path]["dependencies"] & production for path in production})
    return {
        path: {
            "private-interface-access": Counter(item[0] for item in raw[path]["private"]),
            "dependency-cycle": Counter(raw[path]["dependencies"] & set(cycles.get(path, []))),
            "shared-consumers": Counter(raw[path]["consumers"]),
        }
        for path in production
    }


def boundary_delta(graph: Graph, previous: Graph | None) -> dict:
    if previous is None:
        return {"status": "unresolved", "findings": [], "reason": "No retained baseline"}
    before, after = _boundaries(previous), _boundaries(graph)
    errors = _input_errors(graph) + _input_errors(previous)
    findings = []
    for path in sorted(set(before) | set(after)):
        for kind in ("private-interface-access", "dependency-cycle", "shared-consumers"):
            prior = before.get(path, {}).get(kind, Counter())
            current = after.get(path, {}).get(kind, Counter())
            added, removed = current - prior, prior - current
            if not added and not removed:
                continue
            findings.append(
                {
                    "kind": kind,
                    "path": path,
                    "change": "worsened"
                    if added and prior
                    else "introduced"
                    if added
                    else "improved",
                    "previous_count": sum(prior.values()),
                    "candidate_count": sum(current.values()),
                    "added": sorted(added)[:10],
                    "removed": sorted(removed)[:10],
                    "status": ("widened" if added else "narrowed")
                    if kind == "shared-consumers"
                    else "concern"
                    if added
                    else "unresolved"
                    if errors
                    else "improved",
                }
            )
    return {
        "status": "observed",
        "findings": findings[:50],
        "total": len(findings),
        "truncated": len(findings) > 50,
        "unresolved_inputs": errors[:10],
        "note": "Static relations; consumer additions widen impact, not proof of a design defect.",
    }


def obligation(item: dict) -> dict:
    """Persist bounded material requirements, not review transcripts or entire briefs."""
    return {
        "proposal_id": item["proposal_id"],
        "kind": item["kind"],
        "title": item["title"][:120],
        "public_contract": item.get("public_contract", item.get("suggestion", ""))[:160],
        "verification": [text[:100] for text in item.get("verification", [])[:1]],
        "module_ids": item.get("module_ids", [])[:3],
        "assessed_snapshot_id": item.get("assessed_snapshot_id"),
        "predicate": "unresolved-freeform-proposal",
    }


def independent_verification(graph: Graph, previous: Graph | None, checks: list[dict]) -> dict:
    reach = extension.check_reach(graph, changed_paths(graph, previous))
    return {
        "status": "unresolved",
        "observed_check_reach": reach,
        "baseline_check_reach": extension.check_reach(previous, changed_paths(graph, previous))
        if previous
        else None,
        "supplied_checks": [{key: check[key] for key in ("command", "status")} for check in checks],
        "note": "Import reach includes composition roots. Snapshot-matched check reports do not establish isolated component environments, consumer coverage or full behavioral correctness.",
    }
