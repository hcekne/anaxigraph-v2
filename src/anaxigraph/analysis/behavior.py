"""Keep reusable behavior briefs free of source identities; evidence stays separate."""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from anaxigraph.contracts import Graph

from .cache import graph_cached

Matcher = Callable[[str], bool]
_LABELS = r"`[a-z]+`(?:(?:\s*,\s*(?:(?i:and)\s+)?|\s+(?i:and)\s+)`[a-z]+`)*"
_DATA = r"(?i:(?:object|field|key|label|value)s?)"
_DATA_LABELS = re.compile(rf"\b{_DATA}\s+{_LABELS}|{_LABELS}\s+{_DATA}\b")


def _contains(text: str, values: frozenset[str], code: bool = False) -> bool:
    if len(text) > 600:
        raise ValueError("Behavior statement exceeds its existing 600-character limit")
    marks = [match.start() for match in re.finditer(r"\W", text)]
    ends = [*marks, len(text)]
    prefixes = {match.end() for match in re.finditer(r"(?<!\w)[A-Za-z_]\w*\.", text)}
    calls = {match.start() for match in re.finditer(r"\(|\.[A-Za-z_]", text)}
    return any(
        text[start:end] in values
        and (
            not code
            or start in prefixes
            or end in calls
            or text[start - 1 : start] == "`"
            and text[end : end + 1] == "`"
        )
        for start in [0, *(point + 1 for point in marks)]
        for end in ends
        if end > start
    )


@lru_cache(maxsize=4)
def _compile_matcher(identities: tuple[str, ...], names: tuple[str, ...]) -> Matcher:
    literal, code = frozenset(identities), frozenset(names)
    return lambda text: (
        _contains(text, literal)
        or _contains(_DATA_LABELS.sub(lambda match: match[0].replace("`", ""), text), code, True)
    )


@graph_cached
def behavior_matcher(graph: Graph) -> Matcher:
    # No graph hashing or strong graph capture. Annotation-only graph copies
    # share immutable indexes through their bounded structural-identity key.
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
    return _compile_matcher(tuple(sorted(identities)), tuple(sorted(names)))


def check_behaviors(graph: Graph, behaviors: list[str]) -> None:
    matches = behavior_matcher(graph)
    if any(matches(behavior) for behavior in behaviors):
        raise ValueError(
            "Functional statement contains source identity; rewrite it as behavior before Fresh Eyes"
        )


def _annotation_fields(annotation: dict[str, Any]) -> list[tuple[str, Any]]:
    capabilities, contracts = annotation.get("capabilities"), annotation.get("contracts")
    return [
        (f"capabilities[{i}]", text)
        for i, text in enumerate(capabilities if isinstance(capabilities, list) else [])
    ] + [
        (f"contracts[{i}].statement", contract.get("statement"))
        for i, contract in enumerate(contracts if isinstance(contracts, list) else [])
        if isinstance(contract, dict)
    ]


def annotation_behavior_problems(
    graph: Graph, annotations: list[dict[str, Any]] | None = None
) -> dict[str, str]:
    matches: Matcher | None = None
    entries = (
        [(node.id, node.data.get("annotation")) for node in graph.nodes if node.kind == "module"]
        if annotations is None
        else [(annotation.get("node_id", ""), annotation) for annotation in annotations]
    )
    problems = {}
    for identity, annotation in entries:
        if not annotation:
            continue
        matches = matches or behavior_matcher(graph)
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
