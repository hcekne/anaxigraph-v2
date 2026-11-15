"""Link each file's pending references against the checkout's other files.

Runtime dispatch stays uncertain: only names that reach an import are resolved here, and
each file records which modules and names its answers depended on.
"""

from __future__ import annotations

import sys
from collections import defaultdict

from anaxigraph.contracts import Edge, Node
from anaxigraph.indexing.facts import Binding, Module, edges_for, module_id, unknown_node

_REEXPORT_HOPS = 4


def _member(module: Module, name: str, status: str) -> tuple[str | None, str]:
    if not name:
        return module_id(module.path), status
    node = module.definitions.get(name)
    return (node.id, status) if node else (None, "unresolved")


def _rank(anchor: str, here: list[str]) -> tuple[bool, int]:
    """Prefer a root above the importing file, deepest first; then the closest other root."""
    parts = anchor.split("/") if anchor else []
    shared = 0
    while shared < min(len(parts), len(here)) and parts[shared] == here[shared]:
        shared += 1
    return shared == len(parts), shared


class Resolver:
    def __init__(self, modules: list[Module]):
        self.by_name: dict[str, list[tuple[Module, str]]] = defaultdict(list)
        for module in modules:
            for name, anchor in module.names.items():
                self.by_name[name].append((module, anchor))
        # What the answers given since the last link() started depended on.
        self.used: set[str] = set()
        self.asked: set[str] = set()

    def _candidates(self, name: str, source: Module) -> dict[str, Module]:
        """Modules importable under this name from the nearest root to the importing file.

        A standard-library name is local only when its root contains the importing file.
        """
        self.asked.add(name)
        here = source.path.split("/")[:-1]
        standard = name.partition(".")[0] in sys.stdlib_module_names
        best: tuple[bool, int] | None = None
        found: dict[str, Module] = {}
        for module, anchor in self.by_name.get(name, ()):
            rank = _rank(anchor, here)
            if standard and not rank[0]:
                continue
            if best is None or rank > best:
                best, found = rank, {}
            if rank == best:
                found[module.path] = module
        return found

    def find_module(self, name: str, source: Module) -> tuple[Module | None, str]:
        matches = self._candidates(name, source)
        if len(matches) != 1:
            return None, "ambiguous" if matches else "unresolved"
        module = next(iter(matches.values()))
        self.used.add(module.path)
        return module, "resolved"

    def local(self, name: str, source: Module) -> bool:
        """Whether an unlinked import names a package or module of this checkout."""
        return bool(self._candidates(name.partition(".")[0], source))

    def _module_import(
        self, module_name: str, suffix: list[str], source: Module
    ) -> tuple[str | None, str]:
        # import package.module binds package; consume the longest observed module prefix.
        for length in range(len(suffix), -1, -1):
            name = ".".join([module_name, *suffix[:length]])
            module, status = self.find_module(name, source)
            if module:
                return _member(module, ".".join(suffix[length:]), status)
        return None, "unresolved"

    def _symbol_import(
        self,
        module_name: str,
        imported: str,
        suffix: list[str],
        source: Module,
        module_fallback: bool,
        hops: int,
    ) -> tuple[str | None, str]:
        module, status = self.find_module(module_name, source)
        if module:
            node = module.definitions.get(".".join([imported, *suffix]))
            if node:
                return node.id, status
            bindings = module.bindings.get("", {}).get(imported, []) if hops else []
            for binding in bindings:  # The module imported this name itself: a re-export.
                target, found = self.import_target(binding, suffix, module, hops=hops - 1)
                if target:
                    return target, found if len(bindings) == 1 else "ambiguous"
        # `from package import member` can name a submodule instead of a symbol.
        submodule, substatus = self.find_module(f"{module_name}.{imported}".strip("."), source)
        if submodule:
            return _member(submodule, ".".join(suffix), substatus)
        if module and module_fallback:
            return module_id(module.path), status  # The module is certain; the member is a value.
        return None, status if status == "ambiguous" else "unresolved"

    def import_target(
        self,
        binding: Binding,
        suffix: list[str],
        source: Module,
        *,
        module_fallback: bool = False,
        hops: int = _REEXPORT_HOPS,
    ) -> tuple[str | None, str]:
        kind, module_name, imported = binding
        if kind == "module":
            return self._module_import(module_name, suffix, source)
        return self._symbol_import(module_name, imported, suffix, source, module_fallback, hops)


def _path(identity: str) -> str:
    return identity.partition(":")[2].partition("#")[0]


def link(
    module: Module, resolver: Resolver
) -> tuple[list[Edge], dict[str, Node], frozenset[str], frozenset[str]]:
    """Decide the references that go through imports.

    Returns the edges and unresolved targets, then the module paths and the module names the
    answers depended on: a change to any of those is a reason to link this file again.
    """
    resolver.used, resolver.asked = set(), set()
    edges: list[Edge] = []
    external: dict[str, Node] = {}

    def emit(item, target, name, kind, status, internal=False) -> None:
        owner, line, scope = item
        if target is None:
            node = unknown_node(module.path, scope, name, internal)
            external[node.id], target = node, node.id
        edges.extend(edges_for(module.path, owner, target, kind, line, status))

    for kind, owner, *rest in module.pending:
        if kind == "ref":
            relation, line, name, scope, bindings, attributes, ambiguous, unknown = rest
            for binding in bindings:
                target, status = resolver.import_target(tuple(binding), attributes, module)
                if target or unknown:
                    status = "ambiguous" if ambiguous else status
                    emit((owner, line, scope), target, name, relation, status)
        elif kind == "import":
            line, scope, name = rest
            found, status = resolver.find_module(name, module)
            target = module_id(found.path) if found else None
            local = found is None and resolver.local(name, module)
            emit((owner, line, scope), target, name, "imports", status, local)
        else:
            _from_import(module, resolver, owner, rest, emit)
    return edges, external, frozenset(resolver.used), frozenset(resolver.asked)


def _from_import(module: Module, resolver: Resolver, owner: str, rest: list, emit) -> None:
    line, scope, package, shown, alias, relative = rest
    item = (owner, line, scope)
    target, status = resolver.import_target(
        ("symbol", package, alias), [], module, module_fallback=True
    )
    if target is None:
        emit(item, None, shown, "imports", status, relative or resolver.local(package, module))
        return
    source = resolver.find_module(package, module)[0]
    if source and alias in source.bindings.get("", {}) and source.path != _path(target):
        # A re-exported name still loads the module it was imported from.
        emit(item, module_id(source.path), shown, "imports", "resolved")
    emit(item, target, shown, "imports", status)
