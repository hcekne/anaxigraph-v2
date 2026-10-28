"""Small, dependency-free records exchanged by AnaxiGraph components.

The graph contains observations. Annotations and reviews carry their own basis,
evidence and source identity; they never silently become source facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    name: str
    path: str
    line: int = 1
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    kind: str
    path: str
    line: int = 1
    resolution: str = "resolved"


@dataclass(frozen=True)
class Snapshot:
    id: str
    root: str
    revision: str | None
    dirty: bool
    files: dict[str, str]
    created_at: str


@dataclass(frozen=True)
class Graph:
    snapshot: Snapshot
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    diagnostics: tuple[str, ...] = ()
