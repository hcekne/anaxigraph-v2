"""Read one JavaScript or TypeScript file into the facts a Python file gives.

Scope is deliberate: imports, exports, top-level declarations, and where the file uses a name
it imported or declared at top level. No types are checked, no member calls are resolved and
no path aliases are followed; a bare package specifier stays external.
"""

from __future__ import annotations

import functools
import hashlib
from typing import Any

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Parser
from tree_sitter import Node as Syntax

from anaxigraph.contracts import Node
from anaxigraph.indexing.facts import (
    Module,
    edges_for,
    is_test,
    module_id,
    script_names,
    symbol_id,
)

_DECLARED = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "enum_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "interface",
}
_FUNCTIONS = {"arrow_function", "function_expression", "function", "generator_function"}
_NAMES = {"identifier", "type_identifier", "jsx_identifier"}
_CALLED = {"call_expression": "function", "new_expression": "constructor"}
_ELEMENTS = {"jsx_opening_element", "jsx_self_closing_element"}
_HERITAGE = {"class_heritage", "extends_clause", "implements_clause", "extends_type_clause"}


@functools.cache
def _language(suffix: str) -> Language:
    if suffix == "tsx":
        return Language(tree_sitter_typescript.language_tsx())
    if suffix == "ts":
        return Language(tree_sitter_typescript.language_typescript())
    return Language(tree_sitter_javascript.language())


def _text(node: Syntax | None, limit: int = 500) -> str:
    return node.text.decode("utf-8", "replace")[:limit] if node is not None and node.text else ""


def _line(node: Syntax) -> int:
    return node.start_point[0] + 1


def _is(parent: Syntax, field: str, node: Syntax) -> bool:
    found = parent.child_by_field_name(field)
    return found is not None and found.start_byte == node.start_byte and found.type == node.type


class _Reader:
    def __init__(self, path: str):
        self.path, self.owner = path, module_id(path)
        self.module = Module(path, path.rpartition(".")[0], "", script_names(path))
        self.module.language = "javascript"
        self.exported: dict[str, str] = {}  # Name importers use -> the local name behind it.
        # Syntax to scan, with the symbol or module that owns its uses; None for an import.
        self.bodies: list[tuple[str | None, Syntax]] = []
        self.tokens = hashlib.sha256()

    def statement(self, node: Syntax, exported: bool = False) -> None:
        kind = node.type
        if kind == "import_statement":
            self.bodies.append((None, node))
            self._import(node)
        elif kind == "export_statement":
            self._export(node)
        elif kind in _DECLARED:
            self._declare(node, _text(node.child_by_field_name("name")), _DECLARED[kind], exported)
        elif kind in {"lexical_declaration", "variable_declaration"}:
            for item in node.named_children:
                self._variable(item, exported)
        else:
            self.bodies.append((self.owner, node))

    def _variable(self, node: Syntax, exported: bool) -> None:
        name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        if name is None or name.type != "identifier":
            self.bodies.append((self.owner, node))
        elif value is not None and value.type in _FUNCTIONS:
            self._declare(node, _text(name), "function", exported, value)
        else:  # A value, not a symbol: importers of it link to this module.
            if exported:
                self.exported[_text(name)] = _text(name)
            self.bodies.append((self.owner, value or node))

    def _declare(
        self, node: Syntax, name: str, kind: str, exported: bool, shape: Syntax | None = None
    ) -> None:
        if not name:
            self.bodies.append((self.owner, node))
            return
        if exported:
            self.exported[name] = name
        shape = shape or node
        before = (node.parent if exported and node.parent else node).prev_named_sibling
        data: dict[str, Any] = {
            "qualified_name": name,
            "public": exported,
            "public_basis": "export-statement",
            "basis": "observed",
            "end_line": node.end_point[0] + 1,
            "lines_of_code": node.end_point[0] - node.start_point[0] + 1,
            "docstring": _text(before, 400) if _text(before, 3) == "/**" else "",
            "is_test": is_test(self.path),
            "decorators": [],
            "decorator_count": 0,
            "truncated_fields": [],
        }
        if kind == "function":
            parameters = shape.child_by_field_name("parameters")
            returns = _text(shape.child_by_field_name("return_type"))
            data["signature"] = (name + _text(parameters) + returns)[:500]
            data["parameter_count"] = len(parameters.named_children) if parameters else 0
            data["async"] = any(child.type == "async" for child in shape.children)
        previous = self.module.definitions.get(name)
        identity = symbol_id(self.path, name) + (f"@{_line(node)}" if previous else "")
        symbol = Node(identity, kind, name, self.path, _line(node), data)
        self.module.nodes.append(symbol)
        self.module.definitions[name] = symbol
        self.module.edges.extend(
            edges_for(self.path, self.owner, identity, "contains", symbol.line, "resolved")
        )
        self.bodies.append((identity, shape))

    def _bind(self, local: str, spec: str, imported: str, line: int) -> None:
        self.module.bindings[""].setdefault(local, []).append(("symbol", spec, imported))
        shown = f"{spec}#{imported}"
        # Whether a relative path names code or an asset is known only when files are linked.
        self.module.pending.append(["from", self.owner, line, "", spec, shown, imported, False])

    def _import(self, node: Syntax) -> None:
        spec = _text(node.child_by_field_name("source")).strip("'\"`")
        clause = next((c for c in node.named_children if c.type == "import_clause"), None)
        named = False
        for child in clause.named_children if clause else ():
            if child.type == "identifier":
                self._bind(_text(child), spec, "default", _line(node))
                named = True
            elif child.type == "namespace_import":
                local = _text(child.named_children[0] if child.named_children else None)
                self.module.bindings[""].setdefault(local, []).append(("module", spec, ""))
            for item in child.named_children if child.type == "named_imports" else ():
                imported = _text(item.child_by_field_name("name"))
                alias = _text(item.child_by_field_name("alias")) or imported
                self._bind(alias, spec, imported, _line(node))
                named = True
        if not named:  # Loaded for its effect, or as a namespace: the module itself.
            self.module.pending.append(["import", self.owner, _line(node), "", spec])

    def _export(self, node: Syntax) -> None:
        source = node.child_by_field_name("source")
        clause = next((c for c in node.named_children if c.type == "export_clause"), None)
        pairs = [
            (_text(item.child_by_field_name("alias")) or name, name)
            for item in (clause.named_children if clause else ())
            for name in [_text(item.child_by_field_name("name"))]
        ]
        if source is not None:
            self._reexport(node, _text(source).strip("'\"`"), pairs, everything=clause is None)
            return
        self.exported.update(pairs)
        default = any(child.type == "default" for child in node.children)
        declaration = node.child_by_field_name("declaration")
        value = node.child_by_field_name("value")
        if declaration is not None:
            self.statement(declaration, exported=True)
            value = declaration.child_by_field_name("name")
        elif value is not None:
            self.bodies.append((self.owner, value))
        if default:  # A default that names a declaration is that declaration.
            self.exported["default"] = _text(value) if value and value.type in _NAMES else ""

    def _reexport(self, node: Syntax, spec: str, pairs: list, everything: bool) -> None:
        self.bodies.append((None, node))
        for alias, name in pairs:
            self._bind(alias, spec, name, _line(node))
            self.exported[alias] = ""
        if everything:  # export * from: every name of that module is also ours.
            self.module.bindings[""].setdefault("*", []).append(("symbol", spec, "*"))
            self.module.pending.append(["import", self.owner, _line(node), "", spec])

    def uses(self, owner: str | None, root: Syntax) -> None:
        """Where this syntax uses an imported or top-level name, and what else it loads.

        Every token but comments also enters the file's semantic identity.
        """
        imported, own = self.module.bindings[""], self.module.definitions
        stack = [root]
        while stack:
            node = stack.pop()
            if node.child_count:
                if owner and node.type == "call_expression":
                    self._loaded(owner, node)
                stack.extend(node.children)
            elif node.type != "comment":
                self.tokens.update(node.text or b"")
                name = _text(node) if owner and node.type in _NAMES else ""
                if name and (name in imported or name in own):
                    self._use(owner or self.owner, node, name)

    def _loaded(self, owner: str, node: Syntax) -> None:
        """require("x") and import("x") load a module wherever they appear."""
        function, arguments = (
            node.child_by_field_name("function"),
            node.child_by_field_name("arguments"),
        )
        first = arguments.named_children[0] if arguments and arguments.named_children else None
        if function is None or first is None or first.type != "string":
            return
        if function.type == "import" or _text(function, 8) == "require":
            spec = _text(first).strip("'\"`")
            self.module.pending.append(["import", owner, _line(node), "", spec])

    @staticmethod
    def _role(node: Syntax, parent: Syntax) -> tuple[str, list[str]]:
        """How a name is used: called, inherited from, or read, and through which member."""
        if parent.type in _ELEMENTS or _is(parent, _CALLED.get(parent.type, ""), node):
            return "calls", []
        if parent.type in _HERITAGE:
            return "inherits", []
        if parent.type == "member_expression" and _is(parent, "object", node):
            outer = parent.parent
            called = outer is not None and _is(outer, "function", parent)
            member = [_text(parent.child_by_field_name("property"))]
            return ("calls" if called else "references"), member
        return "references", []

    def _use(self, owner: str, node: Syntax, name: str) -> None:
        parent = node.parent
        if parent is None:
            return
        if parent.type not in _ELEMENTS and _is(parent, "name", node):
            return  # The declaration of the name itself.
        kind, attributes = self._role(node, parent)
        bindings = self.module.bindings[""].get(name)
        if bindings is None:
            target = self.module.definitions[name].id
            if target != owner:
                self.module.edges.extend(
                    edges_for(self.path, owner, target, kind, _line(node), "resolved")
                )
            return
        if any(binding[0] != "module" for binding in bindings):
            attributes = []  # A member of an imported symbol is not resolved: link the symbol.
        shown = ".".join([name, *attributes])
        self.module.pending.append(
            ["ref", owner, kind, _line(node), shown, "", [list(b) for b in bindings], attributes]
            + [len(bindings) > 1, False]
        )

    def finish(self) -> list[str]:
        """Give exported aliases their declarations and return the names importers can use."""
        for owner, body in self.bodies:
            self.uses(owner, body)
        definitions = self.module.definitions
        for name, local in self.exported.items():
            if local in definitions:
                definitions[local].data["public"] = True
                definitions[name] = definitions[local]
        return sorted(self.exported)


def parse_script(
    path: str, source: str, fingerprint: str, _depth: int = 0
) -> tuple[Module | None, Node, str | None]:
    """Read one file. A file with syntax errors still gives the facts that could be recovered."""
    suffix = path.rpartition(".")[2]
    lines = source.splitlines()
    data: dict[str, Any] = {
        "language": "typescript" if suffix in {"ts", "tsx"} else "javascript",
        "fingerprint": fingerprint,
        "loc": sum(
            bool(text) and not text.startswith(("//", "/*", "*")) for text in map(str.strip, lines)
        ),
        "physical_lines": len(lines),
        "is_test": is_test(path),
        "basis": "observed",
        "parse_status": "parsed",
        "docstring": "",
        "truncated_fields": [],
    }
    root = Parser(_language(suffix)).parse(source.encode()).root_node
    reader = _Reader(path)
    for statement in root.named_children:
        if statement.type != "comment":
            reader.statement(statement)
    names = reader.finish()
    module = reader.module
    surface = [
        (name, module.definitions[name].data.get("signature"))
        for name in names
        if name in module.definitions
    ]
    data["inert_module"] = all(child.type == "comment" for child in root.named_children)
    data["semantic_fingerprint"] = reader.tokens.hexdigest()
    data["interface_fingerprint"] = hashlib.sha256(repr((names, surface)).encode()).hexdigest()
    data["public_surface"] = {
        "basis": "export-statements",
        "count": len(reader.exported),
        "names": [name[:160] for name in sorted(reader.exported)[:100]],
        "truncated": len(reader.exported) > 100,
    }
    data["function_count"] = sum(node.kind == "function" for node in module.nodes)
    data["class_count"] = sum(node.kind in {"class", "interface"} for node in module.nodes)
    module.seal()
    error = f"{path}: syntax errors; recovered facts may be incomplete" if root.has_error else None
    return module, Node(module_id(path), "module", module.name, path, 1, data), error
