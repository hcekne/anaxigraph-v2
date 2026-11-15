"""How much of a checkout the structural map really links. A blind map must say so."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from anaxigraph.contracts import Graph, Node

_UNREAD = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}


def _a_test(node: Node) -> bool:
    """A file of tests, as opposed to a conftest or a helper kept beside them."""
    stem = PurePosixPath(node.path).stem
    return bool(node.data.get("is_test")) and (stem.startswith("test_") or stem.endswith("_test"))


def _imports(graph: Graph, nodes: dict[str, Node]) -> tuple[int, list[dict[str, Any]]]:
    """Linked imports, and unlinked ones the indexer saw as naming this checkout's own code."""
    linked, unlinked = 0, []
    for edge in graph.edges:
        target = nodes.get(edge.target) if edge.kind == "imports" else None
        if target is not None and target.kind != "external":
            linked += 1
        elif target is not None and target.data.get("internal"):
            unlinked.append({"path": edge.path, "line": edge.line, "name": target.name})
    return linked, unlinked


def _linked_tests(graph: Graph, nodes: dict[str, Node], modules: dict[str, Node]) -> int:
    """Test files with a resolved reference into a module that is not itself a test."""
    tests = {path for path, node in modules.items() if _a_test(node)}
    code = {path for path, node in modules.items() if not node.data.get("is_test")}
    return len(
        {
            edge.path
            for edge in graph.edges
            if edge.path in tests
            and edge.resolution == "resolved"
            and edge.target in nodes
            and nodes[edge.target].path in code
        }
    )


def map_health(graph: Graph) -> dict[str, Any]:
    """Count linked imports between the checkout's own files, and source left unread.

    "Internal" for an unlinked import is the indexer's estimate of intent, not proof.
    """
    nodes = {
        node.id: node for node in graph.nodes if node.kind != "external" or "internal" in node.data
    }
    modules = {node.path: node for node in graph.nodes if node.kind == "module"}
    linked, unlinked = _imports(graph, nodes)
    total = linked + len(unlinked)
    share = round(linked / total, 3) if total else None
    statuses = [node.data.get("parse_status") for node in modules.values()]
    warning = None
    if total >= 20 and linked / total < 0.9:
        warning = (
            f"Only {linked} of {total} imports between this checkout's own files are linked; "
            "dependency, consumer and test answers are incomplete."
        )
    return {
        "internal_imports": {
            "linked": linked,
            "unlinked": len(unlinked),
            "linked_share": share,
            "unlinked_examples": unlinked[:5],
        },
        "modules": {"parsed": statuses.count("parsed"), "parse_errors": statuses.count("error")},
        "unread_source_files": sum(
            PurePosixPath(path).suffix in _UNREAD and path not in modules
            for path in graph.snapshot.files
        ),
        "test_modules": {
            "linked_to_code": _linked_tests(graph, nodes, modules),
            "total": sum(_a_test(node) for node in modules.values()),
        },
        "warning": warning,
    }
