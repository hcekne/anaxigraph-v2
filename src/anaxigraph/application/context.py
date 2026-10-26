"""Snapshot-relative semantic context shared by application operations."""

from __future__ import annotations

import hashlib
import json

from anaxigraph.contracts import Graph


def _dependencies(graph: Graph) -> dict[str, set[str]]:
    owners = {node.id: node.path for node in graph.nodes}
    dependencies = {node.path: {node.path} for node in graph.nodes if node.kind == "module"}
    for edge in graph.edges:
        source, target = owners.get(edge.source), owners.get(edge.target)
        if (
            source in dependencies
            and target in graph.snapshot.files
            and edge.resolution == "resolved"
        ):
            dependencies[source].add(target)
    return dependencies


def context_fingerprints(graph: Graph) -> dict[str, str]:
    interfaces = {
        node.path: node.data.get("interface_fingerprint", graph.snapshot.files.get(node.path))
        for node in graph.nodes
        if node.kind == "module"
    }
    return {
        path: hashlib.sha256(
            json.dumps(
                {
                    p: graph.snapshot.files[p]
                    if p == path
                    else interfaces.get(p, graph.snapshot.files[p])
                    for p in paths
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        for path, paths in _dependencies(graph).items()
    }


def dependency_implementation_fingerprints(graph: Graph) -> dict[str, str]:
    semantics = {
        node.path: node.data.get("semantic_fingerprint", graph.snapshot.files.get(node.path))
        for node in graph.nodes
        if node.kind == "module"
    }
    return {
        path: hashlib.sha256(
            json.dumps(
                {p: semantics.get(p, graph.snapshot.files[p]) for p in paths if p != path},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        for path, paths in _dependencies(graph).items()
    }


def _bounds(limit: int, maximum: int = 100) -> int:
    if type(limit) is not int or not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return limit
