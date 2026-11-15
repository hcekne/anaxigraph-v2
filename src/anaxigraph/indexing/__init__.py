"""The indexing component accepts a checkout and returns immutable source observations."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from itertools import chain
from pathlib import Path

from anaxigraph.contracts import FilePart, Graph, Snapshot
from anaxigraph.indexing import discovery
from anaxigraph.indexing.discovery import SourceCache, capture_sources, discover, worktrees
from anaxigraph.indexing.facts import Indexed, restore
from anaxigraph.indexing.python import import_depth, package_folders, parse_module
from anaxigraph.indexing.resolution import Resolver, link


class IndexSession:
    """One checkout's incremental reader: each file is read once per content and linked again
    only when something its links depended on has changed."""

    def __init__(self, root: Path, *, excluded: tuple[str, ...] = ()) -> None:
        self.root = root.resolve(strict=True)
        if any(not isinstance(pattern, str) or not pattern for pattern in excluded):
            raise ValueError("Exclusions must be nonempty path patterns")
        self.excluded = excluded
        self.sources = SourceCache()
        self.files: dict[str, Indexed] = {}
        self.last_scan: dict[str, int] = {}
        self.last_graph: Graph | None = None
        self.last_parts: tuple[FilePart, ...] = ()
        self.unchanged = False
        self._built = False
        self._seen: tuple[str, dict[str, str]] | None = None
        self._saved: Callable[[], Sequence[FilePart] | None] | None = None

    def scan(self) -> Graph:
        return index(self.root, excluded=self.excluded, session=self)

    def restore(
        self, graph: Graph | None, parts: Callable[[], Sequence[FilePart] | None] | None = None
    ) -> None:
        """Seed saved facts; scan still verifies the complete current input identity.

        `parts` is only called when the checkout has changed since that graph was saved.
        """
        if graph is not None and self.last_graph is None and graph.snapshot.root == str(self.root):
            self.last_graph, self._saved = graph, parts

    def seed(self, parts: Callable[[], Sequence[FilePart] | None]) -> None:
        """Offer facts another checkout saved; each is used only for a file with that content."""
        if self.last_graph is None and not self.files:
            self._saved = parts

    def parts_of(self, graph: Graph) -> tuple[FilePart, ...] | None:
        """The per-file parts of a graph this session built; None for one it only restored."""
        return self.last_parts if graph is self.last_graph and self._built else None

    def _restored(self) -> None:
        saved, self._saved = self._saved, None
        for part in (saved() if saved and not self.files else None) or ():
            item = restore(part, discovery.INDEX_VERSION)
            if item is not None:
                self.files[part.path] = item

    def snapshot(self) -> Snapshot:
        return discover(self.root, self.excluded, self.sources)[0]

    def captured_sources(self, snapshot: Snapshot) -> dict[str, str]:
        """The verified source text of a snapshot, without a second walk when it was just seen."""
        if self._seen is None or self._seen[0] != snapshot.id:
            current, sources, _ = discover(self.root, self.excluded, self.sources)
            if current.id != snapshot.id:
                raise ValueError("Checkout changed after mapping; scan again before preparing")
            self._seen = (current.id, sources)
        return self._seen[1]


def _read(snapshot: Snapshot, contents: dict[str, str], known: dict[str, Indexed]) -> dict:
    """Facts for every source file, reading only those whose content or import root changed."""
    packages = package_folders(snapshot.files)
    current = {}
    for path, source in contents.items():
        identity = (snapshot.files[path], import_depth(path, packages))
        item = known.get(path)
        if item is None or item.identity != identity:
            item = Indexed(identity, *parse_module(path, source, *identity))
        current[path] = item
    return current


def _stale(current: dict[str, Indexed], known: dict[str, Indexed]) -> set[str]:
    """Files to link again: those read anew, and those whose answers could now differ."""
    paths = current.keys() | known.keys()
    touched = {path for path in paths if current.get(path) is not known.get(path)}
    surfaces, names = set(), set()
    for path in touched:
        before = known[path].module if path in known else None
        after = current[path].module if path in current else None
        if (before.interface if before else None) != (after.interface if after else None):
            surfaces.add(path)
        names |= set(before.names if before else ()) ^ set(after.names if after else ())
    return {
        path
        for path, item in current.items()
        if path in touched or item.used & surfaces or item.asked & names
    }


def _link(current: dict[str, Indexed], known: dict[str, Indexed]) -> int:
    """Link again the files whose answers could differ; returns how many had facts to link."""
    stale = _stale(current, known)
    resolver = Resolver([item.module for item in current.values() if item.module])
    for path in stale:
        item, saved = current[path], known.get(path)
        if saved is not None and item is saved:  # Same facts, new answers: leave the saved one.
            item = current[path] = Indexed(saved.identity, saved.module, saved.node, saved.error)
        if item.module is not None:
            item.edges, item.external, item.used, item.asked = link(item.module, resolver)
        item.build(discovery.INDEX_VERSION)
    return sum(current[path].module is not None for path in stale)


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
    if session is None:
        session = IndexSession(root, excluded=excluded)
    session._seen = (snapshot.id, contents)
    previous = session.last_graph
    session.unchanged = previous is not None and previous.snapshot.id == snapshot.id
    counts = {
        "files_read": session.sources.files_read,
        "files_reused": session.sources.files_reused,
        "bytes_read": session.sources.bytes_read,
    }
    if session.unchanged and previous is not None:
        session.last_scan = counts | {"modules_parsed": 0, "modules_resolved": 0}
        return previous
    session._restored()
    known = session.files
    current = _read(snapshot, contents, known)
    read = sum(item is not known.get(path) for path, item in current.items())
    linked = _link(current, known)
    parts = tuple(item.part for item in current.values() if item.part is not None)
    diagnostics.extend(item.error for item in current.values() if item.error)
    if previous is not None and session._built and parts == session.last_parts:
        nodes, edges = previous.nodes, previous.edges  # Only files outside the graph changed.
    else:
        nodes = tuple(chain.from_iterable(part.nodes for part in parts))
        edges = tuple(chain.from_iterable(part.edges for part in parts))
    graph = Graph(snapshot, nodes, edges, tuple(diagnostics))
    session.files, session.last_graph, session.last_parts = current, graph, parts
    session._built = True
    session.last_scan = counts | {"modules_parsed": read, "modules_resolved": linked}
    return graph


__all__ = ["index", "capture_sources", "worktrees", "IndexSession"]
