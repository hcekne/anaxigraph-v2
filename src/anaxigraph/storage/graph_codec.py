"""Canonical encoding of one graph part, and reading of the earlier whole-graph sets.

A part is one file's nodes and edges with the indexer's state. Repeated identities compress
well within a part, and an unchanged file's part is never encoded again.
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Sequence
from typing import Any

from anaxigraph.contracts import Edge, Node


def encode_part(nodes: Sequence[Node], edges: Sequence[Edge], state: str) -> bytes:
    """One compressed part. Annotations live in their own table and are left out."""
    document = {
        "n": [
            [
                node.id,
                node.kind,
                node.name,
                node.path,
                node.line,
                {
                    key: value
                    for key, value in node.data.items()
                    if not key.startswith("annotation")
                },
            ]
            for node in nodes
        ],
        "e": [
            [edge.source, edge.target, edge.kind, edge.path, edge.line, edge.resolution]
            for edge in edges
        ],
        "s": state,
    }
    encoded = json.dumps(
        document, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False
    ).encode()
    return zlib.compress(encoded, 3)


def decode_part(encoded: bytes, *, maximum: int) -> tuple[tuple[Node, ...], tuple[Edge, ...], str]:
    document = _decode(encoded, maximum)
    return (
        tuple(Node(*row) for row in document["n"]),
        tuple(Edge(*row) for row in document["e"]),
        document["s"],
    )


def _decode(encoded: bytes, maximum: int) -> Any:
    decoder = zlib.decompressobj()
    raw = decoder.decompress(encoded, maximum + 1)
    if len(raw) > maximum or not decoder.eof or decoder.unused_data:
        raise ValueError("Stored graph set exceeds its decoded budget or is incomplete")
    return json.loads(raw)


def decode_sets(nodes: bytes, edges: bytes, *, maximum: int) -> tuple[tuple[Node, ...], tuple]:
    """Read the whole-graph node and interned-edge sets written before schema 8."""
    data = _decode(edges, maximum)
    symbols, paths = data["symbols"], data["paths"]
    kinds, resolutions = data["kinds"], data["resolutions"]
    return (
        tuple(
            Node(row["id"], row["kind"], row["name"], row["path"], row["line"], row["data"])
            for row in _decode(nodes, maximum)
        ),
        tuple(
            Edge(symbols[source], symbols[target], kinds[kind], paths[path], line, resolutions[how])
            for source, target, kind, path, line, how in data["edges"]
        ),
    )
