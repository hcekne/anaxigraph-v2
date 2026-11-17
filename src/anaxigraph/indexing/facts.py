"""One file's facts, independent of every other file, and their saved form.

A reader (Python today) turns source into a Module once. References that need no other
file are decided then; those that go through an import stay `pending` until the checkout's
modules are linked. Nothing here parses source or resolves names.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from anaxigraph.contracts import SOURCE_SUFFIXES, Edge, FilePart, Node

Binding = tuple[str, str, str]  # ("module" | "symbol", module name, imported member)


def module_id(path: str) -> str:
    return f"module:{path}"


def symbol_id(path: str, name: str) -> str:
    return f"symbol:{path}#{name}"


SCRIPT_SUFFIXES = SOURCE_SUFFIXES[2:]  # JavaScript and TypeScript, in resolution order.


def is_test(path: str) -> bool:
    p = PurePosixPath(path)
    if "tests" in p.parts or "__tests__" in p.parts:
        return True
    if path.endswith(SCRIPT_SUFFIXES):
        return ".test." in p.name or ".spec." in p.name
    return any(map(p.match, ("test_*", "*_test.py", "conftest.py")))


def script_names(path: str) -> dict[str, str]:
    """Names a JavaScript/TypeScript file answers to: its path, with and without extension,
    and its folder when it is an index. The leading slash keeps them apart from dotted names.
    """
    stem = path.rpartition(".")[0]
    names = {"/" + path: "", "/" + stem: ""}
    if stem.rpartition("/")[2] == "index":
        names["/" + stem.rpartition("/")[0]] = ""
    return names


def module_names(path: str, depth: int | None) -> tuple[str, str, dict[str, str]]:
    """A module's name from its import root, and every name it can be imported under.

    `depth` counts the path parts above the root. Without it the conventional rule applies:
    the repository root, or a src folder. A folder above the root can also be on the path,
    as a namespace package or a test rootdir, so those names are kept with their folder.
    """
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    if depth is None:
        depth = parts.index("src") + 1 if "src" in parts else 0
    name = ".".join(parts[depth:]) or PurePosixPath(path).parent.name
    origin = "/".join(parts[:depth])
    names = {".".join(parts[i:]): "/".join(parts[:i]) for i in range(depth + 1) if parts[i:]}
    names.setdefault(name, origin)
    return name, origin, names


@dataclass
class Module:
    path: str
    name: str
    origin: str
    names: dict[str, str]  # Importable dotted name -> the folder it is importable from.
    nodes: list[Node] = field(default_factory=list)
    definitions: dict[str, Node] = field(default_factory=dict)
    bindings: dict[str, dict[str, list[Binding]]] = field(default_factory=lambda: defaultdict(dict))
    edges: list[Edge] = field(default_factory=list)  # Decided by this file alone.
    external: dict[str, Node] = field(default_factory=dict)
    pending: list[list[Any]] = field(default_factory=list)  # Decided by resolution.link.
    interface: str = ""  # Identity of what other files can resolve against.
    language: str = "python"
    # Used while reading the file, then emptied.
    conditional_bindings: set[tuple[str, str]] = field(default_factory=set)
    bound: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    scopes: dict[int, str] = field(default_factory=dict)
    exports: set[str] | None = None
    export_basis: str = "naming-convention"

    @property
    def package(self) -> str:
        return (
            self.name
            if PurePosixPath(self.path).stem == "__init__"
            else self.name.rpartition(".")[0]
        )

    def seal(self) -> None:
        """Keep what linking needs; drop the working state of one read."""
        self.edges = list(dict.fromkeys(self.edges))
        self.bindings = defaultdict(dict, {"": self.bindings.get("", {})})
        self.conditional_bindings, self.bound, self.scopes = set(), defaultdict(set), {}
        surface = [
            sorted((name, node.id) for name, node in self.definitions.items()),
            sorted((name, sorted(map(list, found))) for name, found in self.bindings[""].items()),
        ]
        self.interface = hashlib.sha256(json.dumps(surface).encode()).hexdigest()


def unknown_node(path: str, scope: str, name: str, internal: bool = False) -> Node:
    return Node(
        f"external:{path}#{scope or '<module>'}:{name}",
        "external",
        name,
        path,
        1,
        {
            "basis": "observed",
            "resolution": "unresolved",
            "scope": scope,
            "note": "Observed reference; target is external or could not be resolved statically.",
            # An import that names this checkout's own code but could not be linked.
            **({"internal": True} if internal else {}),
        },
    )


@dataclass
class Indexed:
    """A file as last read and linked: its facts, and what its links depended on."""

    identity: tuple[str, int]  # Source fingerprint and import depth.
    module: Module | None  # None when the file could not be parsed.
    node: Node
    error: str | None = None
    edges: list[Edge] = field(default_factory=list)  # Decided against other files.
    external: dict[str, Node] = field(default_factory=dict)
    used: frozenset[str] = frozenset()  # Paths of modules whose interface was read.
    asked: frozenset[str] = frozenset()  # Module names looked up, found or not.
    part: FilePart | None = None

    def build(self, version: str) -> FilePart:
        """This file's share of the graph: its own facts first, then what linking added."""
        module = self.module or Module(self.node.path, "", "", {})
        nodes = [self.node, *module.nodes, *module.external.values()]
        local = set(module.edges)
        linked = [edge for edge in dict.fromkeys(self.edges) if edge not in local]
        state = json.dumps(
            {
                "version": version,
                "identity": list(self.identity),
                "error": self.error,
                "split": [len(nodes), len(module.edges)],
                "top": module.bindings.get("", {}),
                "aliases": {
                    name: node.id
                    for name, node in module.definitions.items()
                    if node.data["qualified_name"] != name
                },
                "pending": module.pending,
                "interface": module.interface,
                "used": sorted(self.used),
                "asked": sorted(self.asked),
            },
            separators=(",", ":"),
        )
        nodes += [
            node for identity, node in self.external.items() if identity not in module.external
        ]
        content = [
            version,
            state,
            self.node.data,
            [node.id for node in nodes],
            [[edge.source, edge.target, edge.kind, edge.line, edge.resolution] for edge in linked],
        ]
        key = hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()
        self.part = FilePart(
            self.node.path, key[:32], tuple(nodes), (*module.edges, *linked), state
        )
        return self.part


def restore(part: FilePart, version: str) -> Indexed | None:
    """Rebuild a file's facts from its saved part.

    None when the part carries no state this version of the indexer wrote: the file is then
    read again, so a newer reader never inherits an older reader's facts.
    """
    try:
        state = json.loads(part.state)
        if state["version"] != version:
            return None
        identity = (state["identity"][0], state["identity"][1])
        nodes_end, edges_end = state["split"]
        observed, facts = part.nodes[0], part.nodes[1:nodes_end]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    module = None
    if observed.data.get("parse_status") == "parsed":
        if part.path.endswith(SCRIPT_SUFFIXES):
            module = Module(part.path, observed.name, "", script_names(part.path))
            module.language = "javascript"
        else:
            module = Module(part.path, *module_names(part.path, identity[1]))
        module.nodes = [node for node in facts if node.kind != "external"]
        module.definitions = {node.data["qualified_name"]: node for node in module.nodes}
        known = {node.id: node for node in module.nodes}
        module.definitions.update({name: known[of] for name, of in state["aliases"].items()})
        module.external = {node.id: node for node in facts if node.kind == "external"}
        module.edges = list(part.edges[:edges_end])
        module.bindings[""] = {
            name: [tuple(found) for found in bindings] for name, bindings in state["top"].items()
        }
        module.pending, module.interface = state["pending"], state["interface"]
    return Indexed(
        identity,
        module,
        observed,
        state["error"],
        list(part.edges[edges_end:]),
        {node.id: node for node in part.nodes[nodes_end:]},
        frozenset(state["used"]),
        frozenset(state["asked"]),
        part,
    )
