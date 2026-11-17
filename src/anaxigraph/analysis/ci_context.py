"""Compact structural CI-design context, distinct from task execution inputs."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import PurePosixPath

from anaxigraph.contracts import SOURCE_SUFFIXES, Graph

from .charter import relations


def _area(path: str) -> str:
    parts = PurePosixPath(path).parts[:-1]
    depth = parts.index("src") + 3 if "src" in parts else 2
    return "/".join(parts[:depth]) or "."


def component_context(graph: Graph) -> dict:
    modules, raw = relations(graph)
    sources = set(modules) | {
        path for path in graph.snapshot.files if path.endswith(SOURCE_SUFFIXES)
    }
    groups: dict[str, list] = defaultdict(list)
    for path in sorted(sources):
        groups[_area(path)].append(path)
    links = sorted(
        {
            (_area(path), _area(target))
            for path in raw
            for target in raw[path]["dependencies"]
            if _area(path) != _area(target)
        }
    )
    tests = {path for path, node in modules.items() if node.data.get("is_test")}
    context = {
        "basis": "Provisional source-root/package/directory areas, not accepted component or test-ownership declarations.",
        "areas": [
            {
                "root": area,
                "modules": sum(path in modules for path in items),
                "unparsed_source_count": sum(path not in modules for path in items),
                "sample_paths": items[:8],
                "observed_test_paths": [path for path in items if path in tests][:8],
            }
            for area, items in sorted(groups.items())[:100]
        ],
        "areas_total": len(groups),
        "areas_truncated": len(groups) > 100,
        "area_dependencies": links[:300],
        "dependencies_truncated": len(links) > 300,
        "observed_test_paths": sorted(tests)[:30],
        "unparsed_source_count": len(sources) - len(modules),
        "limitations": "Imports omit dynamic/data dependencies; names and unchanged topology do not establish interface compatibility or test coverage. Use declared task inputs for check selection.",
    }
    return context | {
        "identity": hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
    }
