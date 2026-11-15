"""Resolve lexical names and imported members while keeping runtime dispatch uncertain."""

from __future__ import annotations

import sys
from collections import defaultdict
from collections.abc import Iterator

from anaxigraph.indexing.python import Module, module_id

_REEXPORT_HOPS = 4


def _scopes(source: Module, scope: str) -> Iterator[str]:
    parent = scope
    while True:
        is_class = any(
            node.kind in {"class", "interface"} and node.data["qualified_name"] == parent
            for node in source.nodes
        )
        if parent == scope or not is_class:
            yield parent  # Class bodies are not enclosing lexical scopes of methods.
        if not parent:
            return
        parent = parent.rpartition(".")[0]


def _method_candidate(names: list[str], source: Module, scope: str) -> str | None:
    if names[0] not in {"self", "cls"} or len(names) != 2 or "." not in scope:
        return None
    qualified = scope.rpartition(".")[0] + "." + names[1]
    node = source.definitions.get(qualified)
    return node.id if node else None


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

    def _candidates(self, name: str, source: Module) -> dict[str, Module]:
        """Modules importable under this name from the nearest root to the importing file.

        A standard-library name is local only when its root contains the importing file.
        """
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
        return (
            (next(iter(matches.values())), "resolved")
            if len(matches) == 1
            else (None, "ambiguous" if matches else "unresolved")
        )

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
        binding: tuple[str, str, str],
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

    def targets(
        self, expression_name: str, source: Module, scope: str
    ) -> list[tuple[str | None, str]]:
        names = expression_name.split(".")
        method = _method_candidate(names, source, scope)
        if method:
            return [(method, "ambiguous")]  # Only a possible runtime dispatch target.
        for parent in _scopes(source, scope):
            if names[0] in source.bound.get(parent, set()):
                return [(None, "unresolved")]  # Assignments shadow outer lexical names.
            qualified = ".".join(part for part in (parent, expression_name) if part)
            if qualified in source.definitions:
                node = source.definitions[qualified]
                count = sum(n.data.get("qualified_name") == qualified for n in source.nodes)
                return [(node.id, "ambiguous" if count > 1 else "resolved")]
            bindings = source.bindings.get(parent, {}).get(names[0])
            if bindings:
                conditional = (parent, names[0]) in source.conditional_bindings
                candidates = [self.import_target(item, names[1:], source) for item in bindings]
                if len(bindings) > 1 or conditional:
                    return [(target, "ambiguous") for target, _status in candidates]
                return candidates
        return [(None, "unresolved")]
