"""Focused architectural searches and mechanically checked scope coverage."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from functools import partial
from pathlib import PurePosixPath
from typing import Any, Callable

from anaxigraph.contracts import Graph, Node

from . import fields as field_checks
from .charter import CHARTER, module_facts
from .patterns import production_modules

LENSES = {
    "boundaries": (
        "Find independently changing responsibilities, mixed ownership, private-data access and "
        "accidental coupling across files. Propose smaller public contracts; name private internals "
        "and consumer needs. Check exports/facades first: unresolved references and re-exports "
        "do not establish illegal private access."
    ),
    "packages": (
        "Find cohesive capabilities worth developing as standalone importable tools. Compare "
        "in-place, local package, workspace distribution and separate repository using reuse, "
        "isolated agent context, releases and domain/data ties. Establish existing package_context "
        "first; a root project version is not an independently released internal component. "
        "An already isolated library may need no extraction. Require incremental benefits above "
        "costs; separation need not introduce services/network calls."
    ),
    "verification": (
        "Find evidenced obstacles to independent development/testing: neighbor-dependent fixtures, "
        "shared mutable resources, missing contract checks or broad setup. Specify a local test "
        "seam and retained consumer/shared checks. Empty test links do not establish absent tests. "
        "Without an evidenced obstacle, report unknown; do not invent missing isolation/checks."
    ),
}
PLACEMENTS = (
    "in-place",
    "local-package",
    "workspace-distribution",
    "separate-repository",
    "external-package",
    "service",
)
FINDING_FIELDS = {
    "title",
    "module_ids",
    "priority",
    "placement",
    "public_contract",
    "private_implementation",
    "dependencies",
    "verification",
    "next_step",
    "options",
    "evidence",
    "uncertainty",
}


def _selected_modules(graph: Graph, scope: list[str] | None) -> list[Node]:
    modules = [node for node in graph.nodes if node.kind == "module"]
    selected = (
        [node for node in modules if node.id in scope or node.path in scope]
        if scope
        else production_modules(graph)
    )
    if scope and set(scope) - {value for node in selected for value in (node.id, node.path)}:
        raise ValueError("Architecture scope must name mapped module IDs or paths")
    return selected


def architecture_scopes(
    graph: Graph, scope: list[str] | None = None, batch_size: int = 8
) -> list[list[str]]:
    """Group provisional source neighborhoods, never declare accepted components."""
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("Architecture batch_size must be a positive integer")
    groups: dict[str, list[Node]] = defaultdict(list)
    for node in _selected_modules(graph, scope):
        groups[str(PurePosixPath(node.path).parent)].append(node)
    result: list[list[str]] = []
    for parent in sorted(groups):
        ids = [node.id for node in sorted(groups[parent], key=lambda node: node.path)]
        result.extend(ids[start : start + batch_size] for start in range(0, len(ids), batch_size))
    return result


def _neighbor_view(graph: Graph, node: Node) -> dict[str, Any]:
    view = {
        "id": node.id,
        "path": node.path,
        "is_test": node.data.get("is_test", False),
        "public_surface": module_facts(graph, node)["public_surface"],
        "annotation_status": node.data.get("annotation_status", "missing"),
    }
    if node.data.get("annotation_status") == "current":
        annotation = node.data.get("annotation") or {}
        overview = annotation.get("overview")
        view["overview"] = overview[:180] if isinstance(overview, str) else ""
        view["capabilities"] = [
            value[:180]
            for value in annotation.get("capabilities", [])[:2]
            if isinstance(value, str)
        ]
    return view


def _neighbors(graph: Graph, chosen: list[Node]) -> dict[str, Any]:
    paths = {node.path for node in chosen}
    neighbors: set[str] = set()
    for node in chosen:
        facts = module_facts(graph, node)
        for field in (
            "dependency_paths",
            "consumer_paths",
            "observed_test_paths",
            "consumer_test_paths",
        ):
            neighbors.update(facts[field])
    candidates = sorted(
        (node for node in graph.nodes if node.kind == "module" and node.path in neighbors - paths),
        key=lambda node: node.path,
    )
    return {
        "total": len(candidates),
        "truncated": len(candidates) > 12,
        "modules": [_neighbor_view(graph, node) for node in candidates[:12]],
    }


def _references(graph: Graph, paths: set[str]) -> dict[str, Any]:
    identities = {node.id for node in graph.nodes if node.path in paths}
    relationships = [
        edge for edge in graph.edges if edge.source in identities or edge.target in identities
    ]
    return {
        "relationships": [asdict(edge) for edge in relationships[:200]],
        "relationships_total": len(relationships),
        "relationships_truncated": len(relationships) > 200,
        "unresolved_references": [
            {"name": node.name, "path": node.path, "basis": "external-or-unresolved"}
            for node in graph.nodes
            if node.kind == "external" and node.path in paths
        ][:30],
    }


def architecture_packet(
    graph: Graph,
    chosen: list[Node],
    lens: str,
    module_view: Callable[[Graph, Node], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(lens, str) or lens not in LENSES:
        raise ValueError("Architecture lens must be boundaries, packages or verification")
    if any(node.data.get("annotation_status") != "current" for node in chosen):
        raise ValueError("Architecture scouts need current mapped module understanding")
    packet = {
        "lens": lens,
        "assignment": LENSES[lens],
        "finding_review_policy": (
            "Challenge drafts against source evidence and incremental benefits. Remove repeated "
            "existing boundaries and missing-edge assumptions. Update assessments to keep/unknown "
            "where appropriate and return the complete same response contract."
        ),
        "instruction": (
            "Perform ONLY the assigned search; source neighborhoods are provisional, not accepted "
            "boundaries. Assess EVERY supplied module: finding, keep or unknown. Return at most "
            "three concise, evidence-backed improvements; no extraction quota or no-change essays. "
            "Establish existing exports and packaging first. Each finding names member modules, "
            "a public behavioral contract, hidden implementation, dependencies, costs, evidence "
            "and an incremental next_step beyond existing design. Proactive opportunities need "
            "no bug, but an unestablished gap requires unknown, not a speculative material rewrite. "
            "Challenge drafts and missing-reference claims; retain keep where no benefit is justified. "
            "Return at least TWO placement options: in-place and the proposal; packages also compare local-package, "
            "workspace-distribution and separate-repository. Follow output_limits. Priority: "
            "1 optional, 2 material, 3 highly important. Prefer independently developable/testable "
            "components and small agent contexts; justify abstractions or network services. "
            "Account for shared state, schemas, configuration and resources across import boundaries. "
            "No raw test/CI source or external package source was inspected: missing checks, commands, "
            "versions and external-target resolution remain unknown without supplied evidence. "
            "Verification targets are proposals, never executed/passed results."
        ),
        "charter": CHARTER,
        "output_limits": {
            "findings": 3,
            "title_characters": 140,
            "public_contract_characters": 600,
            "private_implementation_characters": 400,
            "next_step_characters": 400,
            "text_list_items": 12,
            "text_list_item_characters": 300,
            "option_benefit_or_cost_characters": 300,
            "placement_options": 6,
            "minimum_placement_options": 2,
            "evidence_references": 30,
        },
        "modules": [module_view(graph, node) for node in chosen],
        "neighbor_context": _neighbors(graph, chosen),
        **_references(graph, {node.path for node in chosen}),
    }
    schema = {
        "producer": "agent/model identity",
        "lens": lens,
        "assessments": [{"module_id": "supplied module ID", "outcome": "finding|keep|unknown"}],
        "findings": [
            {
                "title": "specific opportunity",
                "module_ids": ["supplied member module ID"],
                "priority": "integer 1–3",
                "placement": "one allowed placement",
                "public_contract": "caller-visible behavior, errors and effects to preserve",
                "private_implementation": "responsibility to hide behind the boundary",
                "dependencies": ["required dependency or shared-resource obligation"],
                "verification": ["proposed check, not an executed result"],
                "next_step": "concrete next action with evidence to inspect",
                "options": [
                    {
                        "placement": "one allowed placement",
                        "benefit": "specific benefit",
                        "cost": "specific cost",
                    }
                ],
                "evidence": [{"path": "member source path", "line": 1}],
                "uncertainty": [],
            }
        ],
        "uncertainty": ["brief missing evidence; required if any assessment is unknown"],
    }
    packet["allowed_placements"] = PLACEMENTS
    return packet, schema


_text = field_checks.text
_strings = field_checks.texts
_assessments = partial(field_checks.assessments, key="module_id")
_members = field_checks.members


def _options(value: Any, lens: str, placement: str) -> None:
    if not isinstance(value, list) or not 2 <= len(value) <= len(PLACEMENTS):
        raise ValueError("Architecture findings need bounded placement comparisons")
    seen = set()
    for option in value:
        if not isinstance(option, dict):
            raise ValueError("Architecture placement option must be an object")
        selected = option.get("placement")
        if selected not in PLACEMENTS or selected in seen:
            raise ValueError("Architecture options contain duplicate or unknown placements")
        seen.add(selected)
        _text(option.get("benefit"), "option benefit", 300)
        _text(option.get("cost"), "option cost", 300)
    required = {"in-place", placement}
    if lens == "packages":
        required.update({"local-package", "workspace-distribution", "separate-repository"})
    if not required <= seen:
        raise ValueError(
            "Architecture options must compare in-place and all required delivery choices"
        )


def _finding(
    finding: Any,
    lens: str,
    scope: set[str],
    graph: Graph,
    evidence_validator: Callable,
) -> set[str]:
    if not isinstance(finding, dict):
        raise ValueError("Architecture finding must be an object")
    members = _members(finding.get("module_ids"), scope)
    for field, maximum in (
        ("title", 140),
        ("public_contract", 600),
        ("private_implementation", 400),
        ("next_step", 400),
    ):
        _text(finding.get(field), field, maximum)
    priority, placement = finding.get("priority"), finding.get("placement")
    if type(priority) is not int or not 1 <= priority <= 3 or placement not in PLACEMENTS:
        raise ValueError("Architecture priority must be 1–3 and placement must be allowed")
    for field in ("dependencies", "verification", "uncertainty"):
        _strings(finding.get(field), field, required=field == "verification")
    _options(finding.get("options"), lens, placement)
    evidence: Any = finding.get("evidence")
    evidence_validator(evidence, graph, required=True)
    paths = {node.path for node in graph.nodes if node.id in members}
    if not paths <= {item["path"] for item in evidence}:
        raise ValueError("Architecture findings need source evidence for every member module")
    return members


def validate_architecture(
    request: dict, result: dict, graph: Graph, evidence_validator: Callable
) -> None:
    lens = request.get("request_options", {}).get("lens")
    if not isinstance(lens, str) or lens not in LENSES or result.get("lens") != lens:
        raise ValueError("Architecture result must match its requested lens")
    outcomes = _assessments(result.get("assessments"), set(request["scope"]))
    findings = result.get("findings")
    if not isinstance(findings, list) or len(findings) > 3:
        raise ValueError("Architecture scouts return zero to three findings")
    members = set()
    for finding in findings:
        members.update(_finding(finding, lens, set(request["scope"]), graph, evidence_validator))
    if members != {identity for identity, outcome in outcomes.items() if outcome == "finding"}:
        raise ValueError("Architecture finding members must match finding assessments exactly")
    _strings(result.get("uncertainty"), "uncertainty", required="unknown" in outcomes.values())


def reconcile_architecture(selected: set[str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    actual: list[tuple[str, str]] = []
    outcomes: Counter = Counter()
    findings = 0
    for row in rows:
        body, result = row["result"], row["result"]["result"]
        lens = body.get("request_options", {}).get("lens")
        if (
            row.get("kind") != "architecture"
            or not isinstance(lens, str)
            or lens not in LENSES
            or result.get("lens") != lens
        ):
            raise ValueError("Architecture reconciliation found an unexpected lens or result kind")
        assessments = _assessments(result.get("assessments"), set(body["scope"]))
        actual.extend((identity, lens) for identity in assessments)
        outcomes.update(assessments.values())
        findings += len(result["findings"])
    expected = {(identity, lens) for identity in selected for lens in LENSES}
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError(
            "Architecture reconciliation needs every declared module/lens pair exactly once"
        )
    return {
        "required_lenses": list(LENSES),
        "completed_lenses": list(LENSES) if selected else [],
        "assessed_pairs": len(actual),
        "expected_pairs": len(expected),
        "findings": findings,
        "outcomes": dict(outcomes),
        "scope_status": "reviewed" if selected else "no_eligible_modules",
        "assessment_note": "Declared lens coverage is checked; findings are proposals, not proven improvements or executed CI checks.",
    }
