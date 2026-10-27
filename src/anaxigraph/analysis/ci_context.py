"""Compact structural CI-design context, distinct from task execution inputs."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import PurePosixPath

from anaxigraph.contracts import Graph


def _area(path: str) -> str:
    parts = PurePosixPath(path).parts
    return parts[0] if len(parts) > 1 else "."


def _area_dependencies(graph: Graph) -> list:
    nodes = {node.id: node for node in graph.nodes}
    return sorted(
        {
            (_area(nodes[edge.source].path), _area(nodes[edge.target].path))
            for edge in graph.edges
            if edge.resolution == "resolved"
            and edge.source in nodes
            and edge.target in nodes
            and _area(nodes[edge.source].path) != _area(nodes[edge.target].path)
        }
    )


def component_context(graph: Graph) -> dict:
    modules = [node for node in graph.nodes if node.kind == "module"]
    groups: dict[str, list] = defaultdict(list)
    for node in modules:
        groups[_area(node.path)].append(node)
    links = _area_dependencies(graph)
    tests = sorted(node.path for node in modules if node.data.get("is_test"))
    unparsed = sorted(
        path
        for path in graph.snapshot.files
        if PurePosixPath(path).suffix in {".js", ".jsx", ".ts", ".tsx"}
    )
    identity = {
        "interfaces": sorted(
            (node.path, node.data.get("interface_fingerprint")) for node in modules
        ),
        "test_paths": tests,
        "unparsed_source_paths": unparsed,
        "area_dependencies": links,
    }
    return {
        "basis": "Inferred top-level source areas, not accepted component or test-ownership declarations.",
        "identity": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
        "areas": [
            {
                "root": area,
                "modules": len(items),
                "sample_paths": sorted(node.path for node in items)[:8],
                "observed_test_paths": [node.path for node in items if node.data.get("is_test")][
                    :8
                ],
            }
            for area, items in sorted(groups.items())[:100]
        ],
        "areas_total": len(groups),
        "areas_truncated": len(groups) > 100,
        "area_dependencies": links[:300],
        "dependencies_truncated": len(links) > 300,
        "observed_test_paths": tests[:30],
        "unparsed_source_count": len(unparsed),
        "limitations": "Imports omit dynamic/data dependencies; test references do not establish coverage. Use declared task inputs for check selection.",
    }
