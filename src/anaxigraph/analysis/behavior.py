"""Keep reusable behavior briefs free of source identities; evidence stays separate."""

from __future__ import annotations

import re
import threading
import weakref
from collections import OrderedDict
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from anaxigraph.contracts import Graph

Matcher = Callable[[str], bool]
_MATCHERS: OrderedDict[int, tuple[weakref.ReferenceType[Graph], Matcher]] = OrderedDict()
_LOCK = threading.Lock()


@lru_cache(maxsize=4)
def _compile_matcher(identities: tuple[str, ...], names: tuple[str, ...]) -> Matcher:
    literal = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, identities)) + r")(?!\w)")
    code_names = "(?:" + "|".join(map(re.escape, names)) + ")"
    code = re.compile(
        r"`" + code_names + r"`|(?<!\w)" + code_names + r"(?=\(|\.[A-Za-z_])"
        r"|(?<!\w)[A-Za-z_]\w*\." + code_names + r"(?!\w)"
    )
    has_names = bool(names)
    return lambda text: bool(literal.search(text) or (has_names and code.search(text)))


def behavior_matcher(graph: Graph) -> Matcher:
    # No graph hashing or strong graph capture. Annotation-only graph copies
    # share compiled patterns through their bounded structural-identity key.
    with _LOCK:
        cached = _MATCHERS.get(id(graph))
        if cached is not None and cached[0]() is graph:
            return cached[1]
        identities = {node.id for node in graph.nodes}
        identities.update(node.path for node in graph.nodes if node.kind == "module")
        identities.update(path for path in graph.snapshot.files if "/" in path or "." in path)
        names = set()
        for node in graph.nodes:
            if node.kind == "external" or not node.name:
                continue
            if "_" in node.name or re.search(r"[a-z0-9][A-Z]|[A-Z]{2}[a-z]|\.", node.name):
                identities.add(node.name)
            names.add(node.name)
        matcher = _compile_matcher(tuple(sorted(identities)), tuple(sorted(names)))
        _MATCHERS[id(graph)] = (weakref.ref(graph), matcher)
        if len(_MATCHERS) > 4:
            _MATCHERS.popitem(last=False)
        return matcher


def check_behaviors(graph: Graph, behaviors: list[str]) -> None:
    matches = behavior_matcher(graph)
    if any(matches(behavior) for behavior in behaviors):
        raise ValueError(
            "Functional statement contains source identity; rewrite it as behavior before Fresh Eyes"
        )


def _annotation_fields(annotation: dict[str, Any]) -> list[tuple[str, Any]]:
    capabilities, contracts = annotation.get("capabilities"), annotation.get("contracts")
    fields = (
        [(f"capabilities[{i}]", text) for i, text in enumerate(capabilities)]
        if isinstance(capabilities, list)
        else []
    )
    if isinstance(contracts, list):
        fields.extend(
            (f"contracts[{i}].statement", contract.get("statement"))
            for i, contract in enumerate(contracts)
            if isinstance(contract, dict)
        )
    return fields


def annotation_behavior_problems(
    graph: Graph, annotations: list[dict[str, Any]] | None = None
) -> dict[str, str]:
    matches = behavior_matcher(graph)
    entries = (
        [(node.id, node.data.get("annotation")) for node in graph.nodes if node.kind == "module"]
        if annotations is None
        else [(annotation.get("node_id", ""), annotation) for annotation in annotations]
    )
    problems = {}
    for identity, annotation in entries:
        if not annotation:
            continue
        field = next(
            (
                name
                for name, text in _annotation_fields(annotation)
                if isinstance(text, str) and matches(text)
            ),
            None,
        )
        if field:
            problems[identity] = (
                f"{field} contains source identity; rewrite it as observable behavior "
                "and keep source references in evidence."
            )
    return problems


def check_annotation_behaviors(graph: Graph, annotations: list[dict[str, Any]]) -> None:
    problems = annotation_behavior_problems(graph, annotations)
    if problems:
        raise ValueError(next(iter(problems.values())))
