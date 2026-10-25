"""Small, dependency-free records exchanged by AnaxiGraph components.

The graph contains observations. Annotations and reviews carry their own basis,
evidence and source identity; they never silently become source facts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class KnowledgeStore(Protocol):
    """The application owns workflow; the store owns atomic persistence."""

    def save_graph(self, graph: Graph) -> None: ...

    def load_graph(self, root: str, snapshot_id: str | None = None) -> Graph | None: ...

    def pin_baseline(self, root: str, snapshot_id: str | None = None) -> str: ...

    def annotate(
        self,
        root: str,
        snapshot_id: str,
        node_id: str,
        annotation: dict[str, Any],
        *,
        expected_digest: str | None = None,
    ) -> bool: ...

    def search(self, root: str, query: str, limit: int = 20) -> list[dict[str, Any]]: ...

    def save_review(
        self, root: str, snapshot_id: str, review_id: str, kind: str, result: dict[str, Any]
    ) -> None: ...

    def reviews(
        self,
        root: str,
        kind: str | None = None,
        *,
        review_id: str | None = None,
        include_requests: bool = True,
    ) -> list[dict[str, Any]]: ...

    def get_review(
        self, root: str, review_id: str, kind: str | None = None
    ) -> dict[str, Any] | None: ...

    def stats(self, root: str) -> dict[str, Any]: ...

    def cache_token(self) -> tuple[int, ...]: ...

    def knowledge_token(self, root: str) -> tuple[int, ...]: ...

    def review_token(self, root: str) -> tuple[int, ...]: ...
