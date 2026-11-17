"""Snapshot-relative semantic context shared by application operations."""

from __future__ import annotations

import hashlib
import json
from functools import partial

from anaxigraph.analysis.charter import relations
from anaxigraph.contracts import Graph


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _fingerprints(graph: Graph, field: str, include_own: bool) -> dict[str, str]:
    values = {
        node.path: node.data.get(field, graph.snapshot.files.get(node.path))
        for node in graph.nodes
        if node.kind == "module"
    }
    return {
        path: _digest(
            {
                p: graph.snapshot.files[p] if p == path else values.get(p, graph.snapshot.files[p])
                for p in row["dependencies"] | {path}
                if include_own or p != path
            }
        )
        for path, row in relations(graph)[1].items()
    }


context_fingerprints = partial(_fingerprints, field="interface_fingerprint", include_own=True)
dependency_implementation_fingerprints = partial(
    _fingerprints, field="semantic_fingerprint", include_own=False
)


def _bounds(limit: int, maximum: int = 100, *, offset: int = 0) -> int:
    if type(limit) is not int or not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    return limit
