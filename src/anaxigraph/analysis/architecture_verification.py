"""Compare observed boundaries without treating advice or supplied checks as proof."""

from __future__ import annotations

from collections import Counter

from anaxigraph.analysis import extension
from anaxigraph.analysis.changes import changed_paths
from anaxigraph.analysis.charter import boundaries, boundary_scope
from anaxigraph.contracts import Graph


def _delta_status(kind, prior, added, errors, rescoped):
    if rescoped:
        return "scope_changed", "unresolved"
    change = "worsened" if added and prior else "introduced" if added else "improved"
    if kind == "shared-consumers":
        return change, "widened" if added else "narrowed"
    return change, "concern" if added else "unresolved" if errors else "improved"


def boundary_delta(graph: Graph, previous: Graph | None, full: bool = False) -> dict:
    if previous is None:
        return {"status": "unresolved", "findings": [], "reason": "No retained baseline"}
    before, after = boundaries(previous), boundaries(graph)
    scope = boundary_scope(graph, previous)
    errors = [
        row
        for row in (*graph.diagnostics, *previous.diagnostics)
        if ".py:" in row or ".pyi:" in row
    ]
    findings = []
    for path in sorted(set(before) | set(after)):
        for kind in ("private-interface-access", "dependency-cycle", "shared-consumers"):
            prior = before.get(path, {}).get(kind, Counter())
            current = after.get(path, {}).get(kind, Counter())
            added, removed = current - prior, prior - current
            if not added and not removed:
                continue
            change, status = _delta_status(kind, prior, added, errors, path in scope[kind])
            findings.append(
                {
                    "kind": kind,
                    "path": path,
                    "change": change,
                    "previous_count": sum(prior.values()),
                    "candidate_count": sum(current.values()),
                    "added": sorted(added)[:10],
                    "removed": sorted(removed)[:10],
                    "status": status,
                }
            )
    findings.sort(
        key=lambda item: (
            item["change"] not in {"introduced", "worsened"},
            item["status"] != "concern",
        )
    )
    rescoped = sorted(scope["shared-consumers"])
    return {
        "status": "observed",
        "findings": findings if full else findings[:50],
        "total": len(findings),
        "truncated": not full and len(findings) > 50,
        "rescoped_paths": rescoped if full else rescoped[:20],
        "rescoped_total": len(rescoped),
        "unresolved_inputs": errors[:10],
        "note": "Static relations; scope_changed reflects production/test scope, not a proven architectural change. Consumer additions widen impact, not proof of a design defect.",
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
