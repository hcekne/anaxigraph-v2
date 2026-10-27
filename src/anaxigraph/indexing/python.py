"""Scoped Python source facts. Dynamic dispatch is explicitly incomplete."""

from __future__ import annotations

import ast
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from anaxigraph.contracts import Edge, Node

if TYPE_CHECKING:
    from anaxigraph.indexing.resolution import Resolver


def module_id(path: str) -> str:
    return f"module:{path}"


def symbol_id(path: str, name: str) -> str:
    return f"symbol:{path}#{name}"


def expression(node: ast.AST | None) -> str:
    return ast.unparse(node)[:240] if node is not None else ""


def reference_name(node: ast.AST) -> str:
    """Describe a reference without copying arbitrary call arguments into a graph ID."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{reference_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return f"{reference_name(node.func)}(...)"
    if isinstance(node, ast.Subscript):
        return f"{reference_name(node.value)}[...]"
    return "<dynamic>"


def is_test(path: str) -> bool:
    p = PurePosixPath(path)
    return "tests" in p.parts or p.name.startswith("test_") or p.name.endswith("_test.py")


def _public_surface(body: list[ast.stmt], exports: set[str] | None = None) -> list[tuple]:
    """Static public declarations, without executable function implementations.

    This is an interface identity, not proof of behavioral equivalence. The full
    syntax digest remains available to disclose dependency implementation edits.
    """
    surface: list[tuple[Any, ...]] = []
    for item in body:
        name = getattr(item, "name", "")
        public = bool(name) and (
            (name in exports if exports is not None else not name.startswith("_"))
            or (name.startswith("__") and name.endswith("__"))
        )
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and public:
            surface.append(
                (
                    "function",
                    item.name,
                    type(item).__name__,
                    ast.dump(item.args),
                    ast.dump(item.returns) if item.returns else None,
                    [ast.dump(part) for part in item.decorator_list],
                    [ast.dump(part) for part in getattr(item, "type_params", [])],
                    item.type_comment,
                    ast.get_docstring(item),
                )
            )
        elif isinstance(item, ast.ClassDef) and public:
            surface.append(
                (
                    "class",
                    item.name,
                    [ast.dump(part) for part in item.bases],
                    [ast.dump(part) for part in item.keywords],
                    [ast.dump(part) for part in item.decorator_list],
                    [ast.dump(part) for part in getattr(item, "type_params", [])],
                    ast.get_docstring(item),
                    _public_surface(item.body),
                )
            )
        elif isinstance(item, (ast.Assign, ast.AnnAssign)):
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            names = sorted(
                n.id
                for target in targets
                for n in ast.walk(target)
                if isinstance(n, ast.Name)
                and (n.id in exports if exports is not None else not n.id.startswith("_"))
            )
            if names:
                surface.append(("binding", ",".join(names), ast.dump(item)))
        elif isinstance(item, (ast.Import, ast.ImportFrom)):
            aliases = [
                (alias.name, alias.asname)
                for alias in item.names
                if (
                    (alias.asname or alias.name.split(".")[0]) in exports
                    if exports is not None
                    else not (alias.asname or alias.name).startswith("_")
                )
            ]
            if aliases:
                surface.append(
                    (
                        "import",
                        repr(aliases),
                        getattr(item, "module", None),
                        getattr(item, "level", 0),
                    )
                )
    return sorted(surface, key=lambda declaration: declaration[:2])


def interface_fingerprint(tree: ast.Module) -> str:
    exports = None
    for item in tree.body:
        if isinstance(item, (ast.Assign, ast.AnnAssign)):
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
                try:
                    declared = ast.literal_eval(item.value) if item.value is not None else None
                    if isinstance(declared, (list, tuple)) and all(
                        isinstance(name, str) for name in declared
                    ):
                        exports = set(declared)
                except (ValueError, TypeError, SyntaxError):
                    pass  # Dynamic exports remain outside the static guarantee.
    value = (
        ast.get_docstring(tree),
        sorted(exports) if exports is not None else None,
        _public_surface(tree.body, exports),
    )
    return hashlib.sha256(repr(value).encode()).hexdigest()


def _assigns_exports(item: ast.Assign | ast.AnnAssign) -> bool:
    targets = item.targets if isinstance(item, ast.Assign) else [item.target]
    return any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets)


def _mutates_exports(item: ast.AST) -> bool:
    if isinstance(item, ast.AugAssign):
        return isinstance(item.target, ast.Name) and item.target.id == "__all__"
    if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute):
        return isinstance(item.func.value, ast.Name) and item.func.value.id == "__all__"
    return False


def _declared_exports(tree: ast.Module) -> tuple[set[str] | None, str]:
    assignments = [
        item
        for item in ast.walk(tree)
        if isinstance(item, (ast.Assign, ast.AnnAssign)) and _assigns_exports(item)
    ]
    if not assignments:
        return None, "naming-convention"
    if (
        len(assignments) != 1
        or assignments[0] not in tree.body
        or any(_mutates_exports(item) for item in ast.walk(tree))
    ):
        return None, "dynamic-__all__"
    try:
        expression_node = assignments[0].value
        value = ast.literal_eval(expression_node) if expression_node is not None else None
        if isinstance(value, (list, tuple)) and all(isinstance(name, str) for name in value):
            return set(value), "literal-__all__"
    except (ValueError, TypeError, SyntaxError):
        pass
    return None, "dynamic-__all__"


def _export_metadata(module: Module) -> dict[str, Any]:
    names = module.exports
    star_import = names is None and "*" in module.bindings[""]
    if names is None:
        names = {
            name
            for name in module.bound[""] | set(module.bindings[""]) | set(module.definitions)
            if not name.startswith("_") and "." not in name and name != "*"
        }
    return {
        "basis": "unresolved-star-import" if star_import else module.export_basis,
        "count": None if star_import else len(names),
        "names": [name[:160] for name in sorted(names)[:100]],
        "truncated": len(names) > 100 or any(len(name) > 160 for name in names),
    }


def _inert_module(tree: ast.Module) -> bool:
    return all(
        isinstance(statement, ast.Pass)
        or (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
        for statement in tree.body
    )


@dataclass
class Module:
    path: str
    tree: ast.Module
    name: str
    origin: str
    names: set[str]
    nodes: list[Node] = field(default_factory=list)
    definitions: dict[str, Node] = field(default_factory=dict)
    bindings: dict[str, dict[str, list[tuple[str, str, str]]]] = field(
        default_factory=lambda: defaultdict(dict)
    )
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


def parse_module(
    path: str, source: str, fingerprint: str
) -> tuple[Module | None, Node, str | None]:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    # Conventional src layouts are import roots, including a nested V2 project.
    prefix = parts.index("src") + 1 if "src" in parts else 0
    name = ".".join(parts[prefix:]) or PurePosixPath(path).parent.name
    origin = "/".join(parts[:prefix])
    data = {
        "language": "python",
        "fingerprint": fingerprint,
        "loc": sum(
            bool(line.strip()) and not line.lstrip().startswith("#") for line in source.splitlines()
        ),
        "physical_lines": len(source.splitlines()),
        "function_count": 0,
        "class_count": 0,
        "is_test": is_test(path),
        "basis": "observed",
        "parse_status": "parsed",
    }
    try:
        tree = ast.parse(source, filename=path, type_comments=True)
        module = Module(path, tree, name, origin, {name, ".".join(parts)})
        _populate(module, data)
    except (SyntaxError, ValueError, RecursionError) as exc:
        data["parse_status"] = "error"
        return (
            None,
            Node(module_id(path), "module", name, path, 1, data),
            f"{path}: {type(exc).__name__}: {exc}",
        )
    return module, Node(module_id(path), "module", name, path, 1, data), None


def _populate(module: Module, data: dict) -> None:
    tree = module.tree
    data["inert_module"] = _inert_module(tree)
    # A dependency's comments, whitespace and source locations do not change its
    # semantics. Preserve behavior-sensitive invalidation without fanning out model
    # calls for byte-only edits. Docstrings and function bodies remain meaningful.
    data["semantic_fingerprint"] = hashlib.sha256(
        ast.dump(tree, annotate_fields=True, include_attributes=False).encode()
    ).hexdigest()
    data["interface_fingerprint"] = interface_fingerprint(tree)
    module.exports, module.export_basis = _declared_exports(tree)
    _Definitions(module).visit(tree)
    data["public_surface"] = _export_metadata(module)
    data["function_count"] = sum(node.kind == "function" for node in module.nodes)
    data["class_count"] = sum(node.kind in {"class", "interface"} for node in module.nodes)
    data["docstring"] = (ast.get_docstring(tree) or "")[:600]
    data["truncated_fields"] = ["docstring"] if len(ast.get_docstring(tree) or "") > 600 else []


def import_module(module: Module, node: ast.ImportFrom) -> str:
    package = module.package.split(".") if module.package else []
    if not node.level:
        return node.module or ""
    package = package[: len(package) - node.level + 1] if node.level <= len(package) else []
    return ".".join([*package, *([node.module] if node.module else [])])


def _annotation_children(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return [ast.parse(node.value, mode="eval").body]
        except (SyntaxError, ValueError, RecursionError):
            return []
    if isinstance(node, ast.Subscript):
        arguments = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
        form = reference_name(node.value).split(".")[-1]
        if form == "Literal":
            arguments = []
        elif form == "Annotated":
            arguments = arguments[:1]
        return [node.value, *arguments]
    if isinstance(node, ast.Call):
        return [node.func]  # Factory arguments are values, not declared type references.
    return list(ast.iter_child_nodes(node))


def _common_metadata(module: Module, node, qualified: str) -> dict:
    docstring = ast.get_docstring(node) or ""
    truncated = ["docstring"] if len(docstring) > 400 else []
    if len(node.decorator_list) > 10 or any(
        len(ast.unparse(item)) > 240 for item in node.decorator_list
    ):
        truncated.append("decorators")
    return {
        "qualified_name": qualified,
        "public": (
            qualified.split(".")[0] in module.exports
            and ("." not in qualified or not node.name.startswith("_"))
            if module.exports is not None
            else not node.name.startswith("_")
        ),
        "public_basis": module.export_basis,
        "basis": "observed",
        "end_line": node.end_lineno,
        "lines_of_code": (node.end_lineno or node.lineno) - node.lineno + 1,
        "docstring": docstring[:400],
        "is_test": is_test(module.path),
        "decorators": [expression(item) for item in node.decorator_list[:10]],
        "decorator_count": len(node.decorator_list),
        "truncated_fields": truncated,
    }


def _class_metadata(node: ast.ClassDef) -> tuple[str, dict, list[str]]:
    kind = (
        "interface"
        if any(expression(base).split(".")[-1] in {"Protocol", "ABC"} for base in node.bases)
        else "class"
    )
    truncated = (
        ["bases"]
        if len(node.bases) > 10 or any(len(ast.unparse(base)) > 240 for base in node.bases)
        else []
    )
    return (
        kind,
        {"bases": [expression(base) for base in node.bases[:10]], "base_count": len(node.bases)},
        truncated,
    )


def _function_metadata(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, dict, list[str]]:
    signature = f"{node.name}({ast.unparse(node.args)})" + (
        f" -> {expression(node.returns)}" if node.returns else ""
    )
    parameters = [
        arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    ]
    truncated = []
    if len(signature) > 500:
        truncated.append("signature")
    if len(parameters) > 50:
        truncated.append("parameters")
    if node.returns is not None and len(ast.unparse(node.returns)) > 240:
        truncated.append("returns")
    return (
        "function",
        {
            "signature": signature[:500],
            "async": isinstance(node, ast.AsyncFunctionDef),
            "parameters": parameters[:50],
            "parameter_count": len(parameters),
            "returns": expression(node.returns),
        },
        truncated,
    )


class _Definitions(ast.NodeVisitor):
    def __init__(self, module: Module):
        self.module = module
        self.scope = ""
        self.conditional = 0

    def _conditional(self, node: ast.AST) -> None:
        self.conditional += 1
        self.generic_visit(node)
        self.conditional -= 1

    visit_If = visit_Try = visit_TryStar = visit_For = _conditional
    visit_AsyncFor = visit_While = visit_Match = _conditional

    def _bind(self, name: str, binding: tuple[str, str, str]) -> None:
        candidates = self.module.bindings[self.scope].setdefault(name, [])
        if binding not in candidates:
            candidates.append(binding)
        if self.conditional:
            self.module.conditional_bindings.add((self.scope, name))

    def _symbol(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        qualified = f"{self.scope}.{node.name}".strip(".")
        data = _common_metadata(self.module, node, qualified)
        kind, details, truncated = (
            _class_metadata(node) if isinstance(node, ast.ClassDef) else _function_metadata(node)
        )
        data.update(details)
        data["truncated_fields"].extend(truncated)
        # Overloads/redefinitions remain separate observations; resolution will be ambiguous.
        previous = self.module.definitions.get(qualified)
        identity = symbol_id(self.module.path, qualified) + (f"@{node.lineno}" if previous else "")
        observed = Node(identity, kind, node.name, self.module.path, node.lineno, data)
        self.module.nodes.append(observed)
        self.module.definitions[qualified] = observed
        self.module.scopes[id(node)] = qualified
        if not isinstance(node, ast.ClassDef):
            args = node.args
            self.module.bound[qualified].update(
                arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)
            )
            self.module.bound[qualified].update(arg.arg for arg in (args.vararg, args.kwarg) if arg)
        parent = self.scope
        self.scope = qualified
        self.generic_visit(node)
        self.scope = parent

    visit_FunctionDef = _symbol
    visit_AsyncFunctionDef = _symbol
    visit_ClassDef = _symbol

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.module.bound[self.scope].add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self._bind(local, ("module", alias.name if alias.asname else local, ""))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "__future__":
            return
        target = import_module(self.module, node)
        for alias in node.names:
            self._bind(
                alias.asname or alias.name,
                ("symbol", target, alias.name),
            )


class Relationships(ast.NodeVisitor):
    def __init__(self, module: Module, resolver: Resolver):
        self.module = module
        self.resolver = resolver
        self.scope = ""
        self.owner = module_id(module.path)
        self.edges: list[Edge] = []
        self.external: dict[str, Node] = {}

    def emit(self, target: str, kind: str, line: int, resolution: str = "resolved") -> None:
        self.edges.append(Edge(self.owner, target, kind, self.module.path, line, resolution))
        if (
            kind in {"calls", "imports"}
            and is_test(self.module.path)
            and not target.startswith("external:")
        ):
            self.edges.append(
                Edge(
                    module_id(self.module.path), target, "tests", self.module.path, line, resolution
                )
            )

    def unknown(self, name: str) -> str:
        identity = f"external:{self.module.path}#{self.scope or '<module>'}:{name}"
        self.external[identity] = Node(
            identity,
            "external",
            name,
            self.module.path,
            1,
            {
                "basis": "observed",
                "resolution": "unresolved",
                "scope": self.scope,
                "note": "Observed reference; target is external or could not be resolved statically.",
            },
        )
        return identity

    def _reference(self, name: str, kind: str, line: int, unknown: bool = True) -> None:
        for target, status in self.resolver.targets(name, self.module, self.scope):
            if target or unknown:
                self.emit(target or self.unknown(name), kind, line, status)

    def _annotation(self, node: ast.AST | None) -> None:
        pending = [node] if node is not None else []
        line = getattr(node, "lineno", 1)
        while pending:
            part = pending.pop()
            if isinstance(part, (ast.Name, ast.Attribute)):
                self._reference(reference_name(part), "references", line)
            else:
                pending.extend(_annotation_children(part))

    def _headers(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                self._reference(expression(base), "inherits", base.lineno)
                self._annotation(base)
            return
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            self._annotation(arg.annotation)
        for optional_arg in (node.args.vararg, node.args.kwarg):
            if optional_arg is not None:
                self._annotation(optional_arg.annotation)
        self._annotation(node.returns)

    def _symbol(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        qualified = self.module.scopes[id(node)]
        symbol = next(
            n
            for n in self.module.nodes
            if n.line == node.lineno and n.data["qualified_name"] == qualified
        )
        self.emit(symbol.id, "contains", node.lineno)
        # Decorators/defaults execute in the enclosing scope, not inside the new function.
        for decorator in node.decorator_list:
            self.visit(decorator)
        if not isinstance(node, ast.ClassDef):
            for default in [*node.args.defaults, *(d for d in node.args.kw_defaults if d)]:
                self.visit(default)
        previous = self.scope, self.owner
        self.owner = symbol.id
        self._headers(node)  # Type names bind in the enclosing scope, even with future annotations.
        self.scope = qualified
        for statement in node.body:
            self.visit(statement)
        self.scope, self.owner = previous

    visit_FunctionDef = _symbol
    visit_AsyncFunctionDef = _symbol
    visit_ClassDef = _symbol

    def visit_Call(self, node: ast.Call) -> None:
        name = reference_name(node.func)
        self._reference(name, "calls", node.lineno)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            return
        self._reference(node.id, "references", node.lineno, unknown=False)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._annotation(node.annotation)
        self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            target, status = self.resolver.find_module(alias.name, self.module)
            self.emit(
                module_id(target.path) if target else self.unknown(alias.name),
                "imports",
                node.lineno,
                status,
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "__future__":
            return
        for alias in node.names:
            target, status = self.resolver.import_target(
                ("symbol", import_module(self.module, node), alias.name), [], self.module
            )
            self.emit(
                target or self.unknown("." * node.level + (node.module or "") + "." + alias.name),
                "imports",
                node.lineno,
                status,
            )
