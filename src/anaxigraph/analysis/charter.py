"""A small architectural charter backed by bounded source observations."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from copy import deepcopy
from typing import Any

from anaxigraph.contracts import Graph, Node

from .cache import graph_cached

CHARTER = (
    {
        "id": "ownership",
        "principle": "Give each responsibility one clear owner.",
        "guidance": "Who owns this behavior and its consumer obligations? Reuse that owner; extract only when responsibilities need independent change.",
    },
    {
        "id": "public-surface",
        "principle": "Keep public interfaces small and explicit.",
        "guidance": "Expose operations callers need. For API/MCP providers, can clients discover supported capabilities and their behavioral contracts? Keep provider details and helpers private.",
    },
    {
        "id": "contracts",
        "principle": "Preserve behavior at component contracts.",
        "guidance": "Publish inputs, outputs, failures, authorization, side effects and state/resource obligations; unchanged syntax or signatures alone do not establish behavioral compatibility.",
    },
    {
        "id": "encapsulation",
        "principle": "Reach components through their public contracts.",
        "guidance": "Keep implementation and owned mutable data private; consumers use published contracts, including shared schemas and configuration.",
    },
    {
        "id": "dependency-direction",
        "principle": "Make dependencies point in a deliberate direction.",
        "guidance": "Remove accidental cycles; enforce declared layer rules rather than guessing intent from folder names.",
    },
    {
        "id": "effects",
        "principle": "Keep side effects at explicit edges.",
        "guidance": "Which runtime registrations, events, shared state/resources or configuration connect otherwise separate code? Contain file, network and persistence effects behind explicit boundaries for local testing.",
    },
    {
        "id": "component-tests",
        "principle": "Make components independently buildable and testable.",
        "guidance": "Can local checks install only this component's test dependencies? Declare build/test inputs and consumer contract checks; independent verification need not mean independent deployment. Keep required project checks; widen for behavioral/auth/schema/runtime/build changes or unknown impact.",
    },
    {
        "id": "change-locality",
        "principle": "Make components independent units of change.",
        "guidance": "Would a thousand more feature requests stay local behind stable capability contracts? Let agents verify cohesive components independently; coordinate contract and shared-resource changes.",
    },
    {
        "id": "extension-points",
        "principle": "Let open-ended families grow by adding files, not by editing shared ones.",
        "guidance": "Keep consumers of growing provider families on one public contract; add implementations without changing shared orchestration. Prefer discovery over repeated registry edits. Preserve validation and authorization. Derive CI scope from observed consumers, not folders.",
    },
    {
        "id": "simplicity",
        "principle": "Use the simplest design that preserves the boundaries.",
        "guidance": "Use cohesive packages with small agent contexts; versioned libraries can live in separate repositories and run in process. Services need a deployment, isolation or scaling benefit.",
    },
    {
        "id": "failure-behavior",
        "principle": "Make failure and uncertainty explicit.",
        "guidance": "Document observable error behavior and evidence limits; missing observations are not proof of safety.",
    },
)

_LIMITATION = (
    "Static references are incomplete for dynamic dispatch and unparsed languages. "
    "Test associations do not establish behavioral coverage or permission to skip required checks. "
    "No layer direction rule has been declared; only observed cycles are reported."
)


def _cycles(adjacency: dict[str, set[str]]) -> dict[str, list[str]]:
    """Iterative strongly connected components, including large dependency chains."""
    visited, order = set(), []
    for start in adjacency:
        pending = [(start, False)]
        while pending:
            node, complete = pending.pop()
            if complete:
                order.append(node)
            elif node not in visited:
                visited.add(node)
                pending.append((node, True))
                pending.extend(
                    (target, False) for target in adjacency[node] if target not in visited
                )
    reverse: dict[str, set[str]] = {path: set() for path in adjacency}
    for source, targets in adjacency.items():
        for target in targets:
            reverse[target].add(source)
    found: set[str] = set()
    result: dict[str, list[str]] = {}
    for start in reversed(order):
        group, frontier = [], [start]
        while frontier:
            node = frontier.pop()
            if node not in found:
                found.add(node)
                group.append(node)
                frontier.extend(reverse[node] - found)
        if len(group) > 1:
            members = sorted(group)
            result.update((path, members) for path in group)
    return result


def _private(node: Node) -> bool:
    if node.kind not in {"function", "method", "class", "interface"}:
        return False
    if node.name.startswith("__") and node.name.endswith("__"):
        return False
    return node.data.get("public", not node.name.startswith("_")) is False


def _relations(graph: Graph, modules: dict[str, Node]):
    nodes = {node.id: node for node in graph.nodes}
    raw: dict[str, dict[str, Any]] = {
        path: {"dependencies": set(), "consumers": set(), "tests": set(), "private": {}}
        for path in modules
    }
    witnesses: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in graph.edges:
        source, target = nodes.get(edge.source), nodes.get(edge.target)
        if source is None or target is None or edge.resolution != "resolved":
            continue
        if source.path == target.path or source.path not in modules or target.path not in modules:
            continue
        raw[source.path]["dependencies"].add(target.path)
        raw[target.path]["consumers"].add(source.path)
        witnesses.setdefault((source.path, target.path), {"path": edge.path, "line": edge.line})
        if edge.kind == "tests":
            raw[target.path]["tests"].add(source.path)
        if _private(target):
            raw[source.path]["private"][(target.id, edge.path, edge.line)] = {
                "target": target.id,
                "path": edge.path,
                "line": edge.line,
                "basis": target.data.get("public_basis", "naming-convention"),
            }
    return raw, witnesses


@graph_cached
def _latest(graph: Graph) -> tuple[Any, Any, Any, Any]:
    """Modules, relations, facts and full boundaries, derived once per graph."""
    modules = {node.path: node for node in graph.nodes if node.kind == "module"}
    raw, witnesses = _relations(graph, modules)
    return modules, raw, *_observations(modules, raw, witnesses)


def relations(graph: Graph) -> tuple[dict[str, Node], dict[str, dict[str, Any]]]:
    """Modules by path and their resolved cross-module dependencies, consumers and tests.

    Shared by every caller for the same graph; treat the result as read-only.
    """
    return _latest(graph)[:2]


def boundaries(graph: Graph) -> dict:
    """Read-only production boundaries; full SCC membership stays out of public facts."""
    return _latest(graph)[3]


def boundary_scope(graph: Graph, previous: Graph | None) -> dict[str, set[str]]:
    """Existing files entering/leaving production, including indirectly affected cycles."""
    before, after = boundaries(previous) if previous else {}, boundaries(graph)
    retained = graph.snapshot.files.keys() & (previous.snapshot.files.keys() if previous else set())
    direct = (before.keys() ^ after.keys()) & retained
    cycles = {
        member
        for view in (before, after)
        for path in direct
        for member in view.get(path, {}).get("cycle", ())
    }
    return {
        "private-interface-access": direct,
        "shared-consumers": direct,
        "dependency-cycle": direct | cycles,
    }


def _observations(modules, raw, witnesses):
    production = {path for path, node in modules.items() if not node.data.get("is_test")}
    cycles = _cycles({path: raw[path]["dependencies"] & production for path in production})
    facts, boundaries = {}, {}
    for path, node in modules.items():
        dependencies, consumers, tests = (
            raw[path][key] for key in ("dependencies", "consumers", "tests")
        )
        cycle = cycles.get(path, [])
        access = sorted(
            raw[path]["private"].values(),
            key=lambda item: (item["path"], item["line"], item["target"]),
        )
        consumer_tests = set().union(*(raw[caller]["tests"] for caller in consumers)) - tests
        facts[path] = {
            "module_id": node.id,
            "public_surface": node.data.get(
                "public_surface",
                {"basis": "unrecorded", "count": None, "names": [], "truncated": False},
            ),
            "dependency_paths": sorted(dependencies)[:20],
            "dependency_count": len(dependencies),
            "dependency_interface_fingerprint": hashlib.sha256(
                json.dumps(
                    [
                        (target, modules[target].data.get("interface_fingerprint"))
                        for target in sorted(dependencies)
                    ]
                ).encode()
            ).hexdigest(),
            "consumer_paths": sorted(consumers)[:20],
            "consumer_count": len(consumers),
            "private_accesses": access[:5],
            "private_access_count": len(access),
            "cycle_paths": cycle[:20],
            "cycle_size": len(cycle),
            "cycle_evidence": [
                witnesses[(path, target)] for target in sorted(dependencies & set(cycle))
            ][:3],
            "observed_test_paths": sorted(tests)[:10],
            "observed_test_count": len(tests),
            "consumer_test_paths": sorted(consumer_tests)[:10],
            "consumer_test_count": len(consumer_tests),
            "limitations": _LIMITATION,
        }
        if path in production:
            boundaries[path] = {
                "private-interface-access": Counter(item[0] for item in raw[path]["private"]),
                "dependency-cycle": Counter(dependencies & set(cycle)),
                "shared-consumers": Counter(consumers),
                "cycle": cycle,
            }
    return facts, boundaries


def module_facts(graph: Graph, module: Node) -> dict[str, Any]:
    """Observed scoped facts, with no annotation, snapshot or execution metadata."""
    return deepcopy(_latest(graph)[2][module.path])


def structural_findings(graph: Graph) -> list[dict[str, Any]]:
    findings = []
    for node in graph.nodes:
        if node.kind != "module":
            continue
        facts = module_facts(graph, node)
        rules = [
            (
                "large-module",
                node.data.get("loc", 0),
                500,
                "Inspect responsibility boundaries; size alone is not a defect.",
            ),
            (
                "high-fan-in",
                facts["consumer_count"],
                15,
                "Check public contract stability before changing this shared dependency.",
            ),
            (
                "private-interface-access",
                0 if node.data.get("is_test") else facts["private_access_count"],
                0,
                "Use the other owner's public contract, or deliberately declare a shared internal seam.",
            ),
            (
                "dependency-cycle",
                facts["cycle_size"],
                1,
                "These resolved dependencies form a cycle; inspect ownership and a one-way contract.",
            ),
        ]
        for kind, measured, threshold, message in rules:
            if measured <= threshold:
                continue
            finding: dict[str, Any] = {
                "kind": kind,
                "entity": node.id,
                "measured": measured,
                "threshold": threshold,
                "status": "concern",
                "message": message,
            }
            if kind == "private-interface-access":
                finding["evidence"] = facts["private_accesses"]
            elif kind == "dependency-cycle":
                finding.update(members=facts["cycle_paths"], evidence=facts["cycle_evidence"])
            findings.append(finding)
    return findings
