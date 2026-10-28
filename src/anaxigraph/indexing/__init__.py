"""The indexing component accepts a checkout and returns immutable source observations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from anaxigraph.contracts import Graph, Snapshot
from anaxigraph.indexing.discovery import SourceCache, capture_sources, discover
from anaxigraph.indexing.python import Relationships, parse_module
from anaxigraph.indexing.resolution import Resolver


class IndexSession:
    """One checkout's bounded incremental parser and verified capture cache."""

    def __init__(self, root: Path, *, excluded: tuple[str, ...] = ()) -> None:
        self.root = root.resolve(strict=True)
        if any(not isinstance(pattern, str) or not pattern for pattern in excluded):
            raise ValueError("Exclusions must be nonempty path patterns")
        self.excluded = excluded
        self.sources = SourceCache()
        self.parsed: dict = {}
        self.relationships: dict = {}
        self.last_scan: dict[str, int] = {}
        self.last_graph: Graph | None = None
        self.unchanged = False

    def scan(self) -> Graph:
        return index(self.root, excluded=self.excluded, session=self)

    def snapshot(self) -> Snapshot:
        return discover(self.root, self.excluded, self.sources)[0]

    def captured_sources(self, snapshot: Snapshot) -> dict[str, str]:
        """Reuse verified captures, including inputs too large for the bounded cache."""
        current, sources, _ = discover(self.root, self.excluded, self.sources)
        if current.id != snapshot.id:
            raise ValueError("Checkout changed after mapping; scan again before preparing")
        return sources


def index(
    root: Path, *, excluded: tuple[str, ...] = (), session: IndexSession | None = None
) -> Graph:
    """Capture source identity and extract Python facts; never execute repository code.

    Resolution is lexical, not a proof of runtime reachability. External targets, dynamic
    dispatch, parse failures and unsupported languages remain explicit incomplete evidence.
    """
    if session is not None and root.resolve() != session.root:
        raise ValueError("Index session belongs to another checkout")
    snapshot, contents, diagnostics = discover(
        root, excluded, session.sources if session is not None else None
    )
    if session is not None:
        previous = session.last_graph
        session.unchanged = False
        if previous is not None and previous.snapshot.id == snapshot.id:
            session.unchanged = True
            session.last_scan = {
                "files_read": session.sources.files_read,
                "files_reused": session.sources.files_reused,
                "bytes_read": session.sources.bytes_read,
                "modules_parsed": 0,
                "modules_resolved": 0,
            }
            return previous
    nodes = []
    modules = []
    edges = []
    parsed = {}
    parsed_count = 0
    for path, source in contents.items():
        cached = session.parsed.get(path) if session is not None else None
        fingerprint = snapshot.files[path]
        if cached is not None and cached[0] == fingerprint:
            module, observation, error = cached[1]
        else:
            module, observation, error = parse_module(path, source, fingerprint)
            parsed_count += 1
        parsed[path] = (fingerprint, (module, observation, error))
        nodes.append(observation)
        if error:
            diagnostics.append(error)
        if module:
            modules.append(module)
            nodes.extend(module.nodes)
    resolver = Resolver(modules)
    universe = hashlib.sha256(
        json.dumps(
            [
                (
                    module.path,
                    module.origin,
                    sorted(module.names),
                    [node.id for node in module.nodes],
                )
                for module in modules
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    external = {}
    relationship_cache = {}
    resolved_count = 0
    for module in modules:
        key = (snapshot.files[module.path], universe)
        cached = session.relationships.get(module.path) if session is not None else None
        if cached is not None and cached[0] == key:
            module_edges, module_external = cached[1:]
        else:
            relationships = Relationships(module, resolver)
            relationships.visit(module.tree)
            module_edges, module_external = relationships.edges, relationships.external
            resolved_count += 1
        relationship_cache[module.path] = (key, module_edges, module_external)
        edges.extend(module_edges)
        external.update(module_external)
    if session is not None:
        session.parsed = parsed
        session.relationships = relationship_cache
        session.last_scan = {
            "files_read": session.sources.files_read,
            "files_reused": session.sources.files_reused,
            "bytes_read": session.sources.bytes_read,
            "modules_parsed": parsed_count,
            "modules_resolved": resolved_count,
        }
    nodes.extend(external.values())
    # A syntax visit can find the same relation more than once; facts are set-valued.
    unique_edges = {(e.source, e.target, e.kind, e.path, e.line, e.resolution): e for e in edges}
    graph = Graph(snapshot, tuple(nodes), tuple(unique_edges.values()), tuple(diagnostics))
    if session is not None:
        session.last_graph = graph
    return graph


__all__ = ["index", "capture_sources", "IndexSession"]
