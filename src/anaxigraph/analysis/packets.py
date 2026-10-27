"""Explicit work contracts for external coding agents.

Fresh Eyes receives only behavior statements with opaque requirement identities.
Current source, paths, symbols, dependencies and prior recommendations stay out.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from importlib.resources import files
from typing import Any

from anaxigraph.contracts import Graph, Node

from .behavior import check_behaviors as _check_behaviors
from .patterns import production_modules

KINDS = {"annotate", "pattern-screen", "patterns", "fresh-eyes"}
DEFAULT_PACKET_BYTES = 2_000_000
DEFAULT_RESULT_BYTES = 8_000_000


def catalogue() -> list[dict[str, Any]]:
    content = files(__package__).joinpath("catalog/patterns.json").read_text(encoding="utf-8")
    return json.loads(content)["cards"]


def _default_scope(graph: Graph, kind: str) -> list[str]:
    modules = (
        production_modules(graph)
        if kind in {"patterns", "pattern-screen"}
        else [node for node in graph.nodes if node.kind == "module"]
    )
    if not modules:
        raise ValueError("No production modules available; request an explicit module scope")
    return (
        [node.id for node in modules]
        if kind in {"fresh-eyes", "pattern-screen"}
        else [modules[0].id]
    )


def _scope(graph: Graph, requested: list[str] | None, kind: str) -> list[Node]:
    modules = [node for node in graph.nodes if node.kind == "module"]
    if not modules:
        raise ValueError("No supported source modules are mapped; scan Python source first")
    if not requested:
        requested = _default_scope(graph, kind)
    chosen = [node for node in modules if node.id in requested or node.path in requested]
    matched = {value for value in requested if any(value in (n.id, n.path) for n in chosen)}
    if len(matched) != len(set(requested)):
        raise ValueError("Scope must name mapped module IDs or repository-relative source paths")
    return chosen


def _annotation_current(node: Node) -> bool:
    return bool(node.data.get("annotation")) and node.data.get("annotation_status") == "current"


def _batches(selected: list[Node], size: int, kind: str) -> list[list[str]]:
    # Marker modules still belong to the covered scope, without needing a design
    # of imaginary behavior when they happen to fall into a batch on their own.
    inert = (
        {node.id for node in selected if node.data.get("inert_module") is True}
        if kind == "fresh-eyes"
        else set()
    )
    ids = [node.id for node in selected if node.id not in inert]
    batches = [ids[i : i + size] for i in range(0, len(ids), size)]
    if inert:
        if batches:
            batches[0].extend(sorted(inert))
        else:
            batches.append(sorted(inert))
    return batches


def plan_scopes(
    graph: Graph,
    kind: str,
    scope: list[str] | None = None,
    *,
    batch_size: int | None = None,
    include_current: bool = False,
) -> list[list[str]]:
    """Plan complete independently retryable coverage, without reading source.

    The default annotation batch is one module, the pattern batch is one module
    against the entire catalogue, and a blind subsystem contains sixteen modules.
    These are scheduling defaults, not limits on the repository or saved analysis.
    """
    if kind not in KINDS:
        raise ValueError("Analysis kind must be annotate, patterns, or fresh-eyes")
    size = batch_size if batch_size is not None else (16 if kind == "fresh-eyes" else 1)
    if type(size) is not int or size < 1:
        raise ValueError("batch_size must be a positive integer")
    if kind in {"patterns", "pattern-screen"} and not scope:
        selected = production_modules(graph)
    else:
        selected = _scope(graph, scope or [n.id for n in graph.nodes if n.kind == "module"], kind)
    if kind == "annotate" and not include_current:
        selected = [node for node in selected if not _annotation_current(node)]
    return _batches(selected, size, kind)


def _check_behavior(graph: Graph, behavior: str) -> None:
    _check_behaviors(graph, [behavior])


def _annotation_packet(
    graph: Graph, chosen: list[Node], symbols: list[Node], sources: dict[str, str] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    ids = {node.id for node in chosen}
    inert = all(node.data.get("inert_module") is True for node in chosen)
    packet: dict[str, Any] = {
        "instruction": (
            "Explain behavior concisely: an overview of about 20 words, a short paragraph of "
            "about 60–90 words, and about 15 words for EVERY supplied function. Give 3–6 key "
            "capabilities and only the few essential behavioral contracts; avoid repeating "
            "every helper as another capability or obligation. "
            "Use source evidence for claims; distinguish declared from inferred obligations. "
            "Capabilities AND contract statements must describe observable behavior without "
            "implementation names, internal classes/methods, source paths or current module "
            "boundaries, for later blind design. Keep source references only in the separate "
            "evidence field, never the contract statement. Do not duplicate "
            "signatures or invent guarantees. List uncertainty. Modules observed as inert_module "
            "contain only documentation, comments or pass statements: describe their marker role "
            "honestly and return capabilities: [], functions: [] and contracts: [], never null "
            "or invented application behavior. Other modules require observable capabilities."
        ),
        "modules": [asdict(node) for node in chosen],
        "functions": [asdict(node) for node in symbols],
        "relationships": [
            asdict(edge) for edge in graph.edges if edge.source in ids or edge.target in ids
        ][:200],
        "source": {},
    }
    for node in chosen:
        if sources is None or node.path not in sources:
            raise ValueError("Annotation needs captured source supplied by the application")
        packet["source"][node.path] = sources[node.path]
    schema = {
        "annotations": [
            {
                "node_id": "module ID",
                "overview": "sentence",
                "detail": "short paragraph",
                "capabilities": [] if inert else ["behavior"],
                "functions": [{"node_id": "function ID", "overview": "sentence"}]
                if symbols
                else [],
                "contracts": []
                if inert
                else [
                    {
                        "statement": "observable obligation without source identities",
                        "basis": "declared|inferred",
                        "evidence": [{"path": "relative path", "line": 1}],
                    }
                ],
                "uncertainty": ["unresolved question"],
            }
        ],
        "producer": "agent/model identity",
    }
    return packet, schema


def _pattern_cards(pattern_ids: list[str] | None) -> list[dict[str, Any]]:
    cards = catalogue()
    if pattern_ids is not None:
        if not isinstance(pattern_ids, list) or any(not isinstance(p, str) for p in pattern_ids):
            raise ValueError("pattern_ids must name distinct catalogue cards")
        wanted = set(pattern_ids)
        if len(wanted) != len(pattern_ids) or wanted - {c["id"] for c in cards}:
            raise ValueError("pattern_ids must name distinct catalogue cards")
        cards = [card for card in cards if card["id"] in wanted]
    return cards


def _pattern_packet(
    graph: Graph, chosen: list[Node], pattern_ids: list[str] | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected_paths = {node.path for node in chosen}
    selected_ids = {node.id for node in graph.nodes if node.path in selected_paths}
    owners = {node.id: node.path for node in graph.nodes}
    cards = _pattern_cards(pattern_ids)
    packet = {
        "instruction": (
            "Independently advocate for EACH catalogue pattern against EACH supplied module. "
            "Look for useful alternate responsibility, state, interface and control-flow designs, "
            "not just existing defects. Give opportunity 0–3 (0=no useful benefit, 3=strong), "
            "or null when evidence is insufficient. Explain concrete pros AND costs for every "
            "pair, including rejections. Compare a simple local alternative. Do not manufacture "
            "a problem to justify a pattern. Separate current_fit (present/partial/absent/unknown) "
            "from incremental opportunity. A pattern already implemented well earns no change "
            "opportunity merely because it is a good fit. Positive scores require a specific "
            "proposed_change and evidence for its benefit beyond current behavior. Check existing "
            "language/framework guarantees before proposing equivalent behavior. No card or module may be skipped."
        ),
        "catalogue": cards,
        "modules": [asdict(node) for node in chosen]
        if pattern_ids is None
        else [_screen_module(graph, node) for node in chosen],
        "symbols": [
            asdict(node)
            for node in graph.nodes
            if node.id in selected_ids and node.kind not in {"module", "external"}
        ],
        "connectivity": [
            {
                "module_id": node.id,
                "consumer_modules": len(
                    {
                        owners.get(edge.source)
                        for edge in graph.edges
                        if owners.get(edge.target) == node.path
                        and owners.get(edge.source) != node.path
                    }
                ),
                "dependency_modules": len(
                    {
                        owners.get(edge.target)
                        for edge in graph.edges
                        if owners.get(edge.source) == node.path
                        and owners.get(edge.target) != node.path
                    }
                ),
            }
            for node in chosen
        ],
        "relationships": [
            asdict(edge)
            for edge in graph.edges
            if edge.source in selected_ids or edge.target in selected_ids
        ][:300],
        "evidence_limit": "Static Python graph and saved annotations; inspect source for verification.",
    }
    if pattern_ids is not None:
        from .charter import CHARTER

        packet["charter"] = CHARTER
    schema = {
        "evaluations": [
            {
                "module_id": "module ID",
                "pattern_id": "catalogue ID",
                "opportunity": "integer 0–3 or null",
                "current_fit": "present|partial|absent|unknown",
                "proposed_change": "specific incremental improvement, or no change",
                "pros": ["specific benefit or none"],
                "cons": ["cost or counterevidence"],
                "alternative": "simpler approach",
                "evidence": [{"path": "relative path", "line": 1}],
            }
        ],
        "producer": "agent/model identity",
    }
    return packet, schema


def _screen_module(graph: Graph, node: Node) -> dict[str, Any]:
    from .charter import module_facts

    facts = module_facts(graph, node)
    annotation = node.data.get("annotation", {})
    dependencies = set(facts["dependency_paths"])
    return {
        "id": node.id,
        "path": node.path,
        "fingerprint": graph.snapshot.files[node.path],
        "brief": {
            key: annotation[key]
            for key in ("overview", "detail", "capabilities", "contracts", "uncertainty")
            if key in annotation
        },
        "facts": facts,
        "neighbor_contracts": [
            {
                "path": peer.path,
                "contracts": peer.data.get("annotation", {}).get("contracts", []),
            }
            for peer in graph.nodes
            if peer.kind == "module" and peer.path in dependencies
        ],
    }


def _screen_packet(graph: Graph, chosen: list[Node]) -> tuple[dict[str, Any], dict[str, Any]]:
    from .charter import CHARTER

    missing = [node.path for node in chosen if not _annotation_current(node)]
    if missing:
        raise ValueError(
            f"Pattern screening needs current module briefs; annotate first: {missing[:5]}"
        )
    return {
        "instruction": (
            "Consider EVERY compact catalogue card for EACH module against the architecture "
            "charter, observed facts and current brief. Select only IDs worth a stronger detailed "
            "review, normally 3–10 when justified, at most 10 and zero when no useful opportunity "
            "merits review. Include plausible but uncertain candidates for stronger judgment; "
            "an already proven problem is not required. Never force a minimum or manufacture "
            "findings. An existing good fit "
            "is not itself an improvement. Include opportunities for simpler local code and "
            "clearer responsibilities/interfaces. Python functions and modules can implement "
            "patterns without classes. No pattern is mechanically excluded here. Return IDs "
            "only, with no ratings, prose or explanations; use pattern_ids: [] when none are "
            "selected. Unselected is not proven irrelevant."
        ),
        "catalogue": [
            {key: card[key] for key in ("id", "name", "intent", "questions")}
            for card in catalogue()
        ],
        "charter": CHARTER,
        "modules": [_screen_module(graph, node) for node in chosen],
        "evidence_limit": "Observed source facts and saved interpretations; screening does not establish applicability.",
    }, {
        "producer": "agent/model identity",
        "selections": [{"module_id": "module ID", "pattern_ids": ["catalogue ID"]}],
    }


def _fresh_packet(graph: Graph, chosen: list[Node]) -> tuple[dict[str, Any], dict[str, Any]]:
    missing = [n.path for n in chosen if not _annotation_current(n)]
    if missing:
        raise ValueError(
            f"Fresh Eyes needs current behavior annotations; missing/stale: {missing[:5]}"
        )
    requirements: list[dict[str, Any]] = []
    for node in chosen:
        if node.data.get("inert_module") is True:
            continue
        annotation = node.data["annotation"]
        for capability in annotation.get("capabilities", []):
            requirements.append({"id": f"R{len(requirements) + 1}", "behavior": capability})
        for contract in annotation.get("contracts", []):
            requirements.append(
                {
                    "id": f"R{len(requirements) + 1}",
                    "behavior": contract["statement"],
                    "basis": contract["basis"],
                }
            )
    if not requirements:
        raise ValueError("No functional requirements available; annotate capabilities first")
    _check_behaviors(graph, [requirement["behavior"] for requirement in requirements])
    packet = {
        "instruction": (
            "You are an independent product designer. You receive BEHAVIOR ONLY. Do not inspect "
            "source, repository files or existing architecture. Propose THREE materially different "
            "ways to deliver ALL requirements. Compare responsibility, state and failure ownership; "
            "justify added complexity and explain a minimal option. Preserve the original behavior. "
            "Each design must list every requirement ID and supply its own high-level Mermaid diagram. "
            "Do not collapse the alternatives into a winner or claim tested implementation. "
            "Also return functional_summary (one sentence) and functional_requirements "
            "(at most eight behavior-only sentences). These must describe the purpose and "
            "observable guarantees, not any proposed design or implementation. They form "
            "the behavior brief for an independent product-level design, never a summary "
            "of the current code structure. Return functional_coverage as an aligned list "
            "of requirement-ID lists: each functional brief sentence names the requirements "
            "it preserves, and their union must cover EVERY supplied requirement."
        ),
        "requirements": requirements,
        "blindness": "No source, source paths, symbols, observed dependency topology or prior reviews.",
    }
    schema = {
        "alternatives": [
            {
                "name": "distinct design",
                "design": "concrete explanation",
                "requirements": ["ALL requirement IDs"],
                "pros": ["benefit"],
                "cons": ["cost"],
                "diagram": "flowchart TB ...",
            }
        ],
        "producer": "agent/model identity",
        "functional_summary": "one sentence describing observable purpose",
        "functional_requirements": ["at most eight concise observable requirements"],
        "functional_coverage": [["requirement IDs preserved by the aligned brief sentence"]],
    }
    return packet, schema


def build_request(
    graph: Graph,
    kind: str,
    scope: list[str] | None = None,
    sources: dict[str, str] | None = None,
    *,
    max_packet_bytes: int = DEFAULT_PACKET_BYTES,
    max_result_bytes: int = DEFAULT_RESULT_BYTES,
    pattern_ids: list[str] | None = None,
    level: str = "subsystem",
) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError("Analysis kind must be annotate, patterns, or fresh-eyes")
    chosen = _scope(graph, scope, kind)
    ids = {node.id for node in chosen}
    symbols = [
        node
        for node in graph.nodes
        if node.path in {n.path for n in chosen} and node.kind in {"function", "method"}
    ]
    if kind == "annotate":
        packet, schema = _annotation_packet(graph, chosen, symbols, sources)
    elif kind == "patterns":
        packet, schema = _pattern_packet(graph, chosen, pattern_ids)
    elif kind == "pattern-screen":
        packet, schema = _screen_packet(graph, chosen)
    else:
        packet, schema = _fresh_packet(graph, chosen)
    options = {
        "max_packet_bytes": max_packet_bytes,
        "max_result_bytes": max_result_bytes,
        "pattern_ids": pattern_ids,
        "level": level,
    }
    if kind == "fresh-eyes":
        packet["level"] = level
    return _request(graph, kind, sorted(ids), packet, schema, symbols, chosen, options)


def _request(graph, kind, scope, packet, schema, symbols, chosen, options):
    for field in ("max_packet_bytes", "max_result_bytes"):
        if type(options[field]) is not int or options[field] < 1024:
            raise ValueError(f"{field} must be an integer of at least 1024 bytes")
    digest = hashlib.sha256(
        json.dumps(
            {
                "schema": 2,
                "snapshot": graph.snapshot.id,
                "kind": kind,
                "scope": scope,
                "packet": packet,
                "options": options,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:24]
    # Only the task packet is delivered to blind designers; the envelope is for the
    # orchestrating agent. Source-aware comparison is a later, separate operation.
    request = {
        "request_id": digest,
        "kind": kind,
        "snapshot_id": graph.snapshot.id,
        "scope": scope,
        "packet": packet,
        "response_schema": schema,
        "function_ids": {n.id: [s.id for s in symbols if s.path == n.path] for n in chosen},
        "request_options": options,
        "max_result_bytes": options["max_result_bytes"],
    }
    if len(json.dumps(request).encode()) > options["max_packet_bytes"]:
        raise ValueError(
            "Analysis batch exceeds its configured input budget; split this batch or raise max_packet_bytes"
        )
    return request


def build_product_request(
    graph: Graph,
    children: list[dict[str, Any]],
    *,
    max_packet_bytes: int = DEFAULT_PACKET_BYTES,
    max_result_bytes: int = DEFAULT_RESULT_BYTES,
) -> dict[str, Any]:
    """Design the product from bounded blind functional briefs, never source.

    Children contain full saved ``request`` and accepted ``result`` values. The
    request envelope records exact module coverage and child identities; only
    ``packet`` and ``response_schema`` are delivered to the blind designer.
    Recursive groups make the same operation usable beyond one model's context.
    """
    from .validation import validate_result

    if not isinstance(children, list) or not 1 <= len(children) <= 16:
        raise ValueError("Product synthesis needs between one and sixteen child briefs")
    scope: set[str] = set()
    child_ids: list[str] = []
    requirements: list[dict[str, Any]] = []
    mapped = {node.id for node in graph.nodes if node.kind == "module"}
    for child in children:
        request, result = child["request"], child["result"]
        if request["kind"] != "fresh-eyes" or request["snapshot_id"] != graph.snapshot.id:
            raise ValueError("Product inputs must be current Fresh Eyes requests")
        if request["request_id"] in child_ids:
            raise ValueError("Product inputs must not repeat a child review")
        child_scope = request.get("scope")
        if (
            not isinstance(child_scope, list)
            or not child_scope
            or len(set(child_scope)) != len(child_scope)
            or set(child_scope) - mapped
            or scope.intersection(child_scope)
        ):
            raise ValueError("Product children must cover distinct mapped module scopes")
        validate_result(request, result, graph)
        child_ids.append(request["request_id"])
        scope.update(child_scope)
        brief = result.get("functional_requirements")
        if not isinstance(brief, list) or not 1 <= len(brief) <= 8:
            raise ValueError("Each product input requires one to eight functional_requirements")
        if "functional_coverage" not in result:
            raise ValueError("Each product input requires explicit functional_coverage")
        for behavior in brief:
            if not isinstance(behavior, str) or not behavior.strip() or len(behavior) > 600:
                raise ValueError("Functional requirements must be concise behavior statements")
            requirements.append({"id": f"R{len(requirements) + 1}", "behavior": behavior})
    _check_behaviors(graph, [item["behavior"] for item in requirements])
    # Reuse the blind design contract, not the source-aware module packet.
    covered = [n for n in graph.nodes if n.id in scope and n.kind == "module"]
    packet, schema = _fresh_packet(graph, covered)
    packet["requirements"] = requirements
    packet["level"] = "product"
    packet["instruction"] += (
        " This is a higher-level product design from functional briefs. Recover the overall "
        "user purpose, challenge unnecessary complexity, and compare three coherent complete "
        "architectures. Functional briefs describe behavior, not component boundaries to preserve."
    )
    options = {
        "max_packet_bytes": max_packet_bytes,
        "max_result_bytes": max_result_bytes,
        "pattern_ids": None,
        "level": "product",
    }
    request = _request(graph, "fresh-eyes", sorted(scope), packet, schema, [], [], options)
    # Child result digests make revalidation sensitive to a revised functional brief.
    lineage = [
        {
            "request_id": child["request"]["request_id"],
            "result_digest": hashlib.sha256(
                json.dumps(child["result"], sort_keys=True, allow_nan=False).encode()
            ).hexdigest(),
        }
        for child in children
    ]
    request["children"] = lineage
    request["request_id"] = hashlib.sha256(
        (request["request_id"] + json.dumps(lineage, sort_keys=True)).encode()
    ).hexdigest()[:24]
    if len(json.dumps(request).encode()) > max_packet_bytes:
        raise ValueError(
            "Product batch exceeds its configured input budget; split its child briefs"
        )
    return request
