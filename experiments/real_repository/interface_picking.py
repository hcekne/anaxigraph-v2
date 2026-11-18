"""Would picking by changed names be worth it? A trial rule, replayed before any product code.

Today a top-level change to a file picks the tests of every file that uses that file. The
trial rule asks which top-level names changed, and follows only the files that use those
names; through a file that passes a name on, it follows that name. It is replayed over the
same past changes as the reach report, in two variants:

- strict: falls back to today's rule when code outside any definition changed, a name was
  removed, or a changed definition may register itself somewhere (a decorator that is not
  known to be plain, a class with a base class from the project);
- lenient: never falls back for those, which shows what understanding them could add;
- fields: strict, and where a change only adds or alters plain data members of a class
  (a settings field with a default, say), it follows the files that mention those members
  by name, in code or in data, instead of every file that uses the class.

    uv run python experiments/real_repository/interface_picking.py /scratch/clone --suite backend

Nothing is executed. This is a measurement: the rule is not in the product.
"""

from __future__ import annotations

import argparse
import ast
import collections
import json
import posixpath
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from anaxigraph.analysis import test_selection as selection
from anaxigraph.indexing.python import _outlives_its_call

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reach_report  # noqa: E402

PLAIN = {
    "staticmethod", "classmethod", "property", "dataclass", "abstractmethod", "contextmanager",
    "asynccontextmanager", "overload", "final", "override", "wraps", "total_ordering",
    "lru_cache", "cache", "cached_property",
}  # fmt: skip
BUILT_IN = {
    "object", "Exception", "BaseException", "ValueError", "RuntimeError", "TypeError", "KeyError",
    "LookupError", "Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "Protocol", "NamedTuple",
    "TypedDict", "ABC", "Generic", "BaseModel", "str", "int", "dict", "list", "tuple",
}  # fmt: skip


def last(node: ast.AST) -> str:
    """The last word of a dotted name or of the function a call names."""
    node = node.func if isinstance(node, ast.Call) else node
    node = node.value if isinstance(node, ast.Subscript) else node
    return node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "?")


def registers(item: ast.stmt) -> bool:
    """Whether defining this may change something other than the name it binds."""
    decorators = getattr(item, "decorator_list", [])
    if any(last(decorator) not in PLAIN for decorator in decorators):
        return True
    if isinstance(item, ast.ClassDef):
        return bool(item.keywords) or any(last(base) not in BUILT_IN for base in item.bases)
    return False


def bound(item: ast.stmt) -> set[str]:
    """Top-level names a statement binds or changes in place; none for anything else."""
    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {item.name}
    if isinstance(item, ast.Import):
        return {alias.asname or alias.name.split(".")[0] for alias in item.names}
    if isinstance(item, ast.ImportFrom):
        names = {alias.asname or alias.name for alias in item.names}
        return set() if "*" in names else names
    if isinstance(item, ast.Expr):
        return {"__doc__"} if isinstance(item.value, ast.Constant) else set()
    found = set()
    for node in ast.walk(item):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            found.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            found |= bound(node)
        elif isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, ast.Store):
            while isinstance(node, (ast.Attribute, ast.Subscript)):
                node = node.value
            if isinstance(node, ast.Name):
                found.add(node.id)  # Changed in place: `registry["x"] = ...`.
    return found


class Shape:
    """What one version of a file sets up when it is imported, name by name."""

    def __init__(self, source: str, whole: bool):
        tree = ast.parse(source)
        if not whole:  # As the import fingerprint sees it: plain function bodies do not count.
            pending: list[ast.AST] = [tree]
            while pending:
                for node in ast.iter_child_nodes(pending.pop()):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if _outlives_its_call(node):
                            continue
                        node.body = []
                    pending.append(node)
        self.names: dict[str, str] = collections.defaultdict(str)
        self.refers: dict[str, set[str]] = collections.defaultdict(set)
        self.registering: set[str] = set()
        self.unnamed = ""
        for item in tree.body:
            names, text = bound(item), ast.dump(item)
            if not names:
                self.unnamed += text
            loads = {
                node.id
                for node in ast.walk(item)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            }
            for name in names:
                self.names[name] += text
                self.refers[name] |= loads
                if registers(item):
                    self.registering.add(name)

    def carried(self, changed: set[str]) -> set[str]:
        """Those names, and every name whose import-time code refers to one of them."""
        found = set(changed)
        while True:
            more = {name for name, refers in self.refers.items() if refers & found} - found
            if not more:
                return found
            found |= more


def changed_names(before: str | None, after: str, whole: bool, strict: bool) -> set[str] | str:
    """The top-level names a change touches, or the reason they cannot be told apart."""
    try:
        new = Shape(after, whole)
        old = Shape(before, whole) if before is not None else None
    except (SyntaxError, ValueError, RecursionError):
        return "unreadable"
    if old is None:
        return "new file"
    if old.unnamed != new.unnamed:
        return "code outside any definition changed"
    names = old.names.keys() | new.names.keys()
    changed = {name for name in names if old.names.get(name) != new.names.get(name)}
    carried = new.carried(changed)
    if strict and changed - new.names.keys():
        return "a name was removed"
    if strict and carried & (old.registering | new.registering):
        return "a definition that may register itself changed"
    return carried


INERT = {"BaseSettings", "BaseModel", "object"}  # Bases that keep no list of subclasses.


def _members(item: ast.ClassDef) -> tuple[dict[str, str], str] | None:
    """A class's plain data members with a value, and everything else of it as text; None
    when the class may register itself or a member has no value to fall back on."""
    plain = all(last(base) in INERT or last(base).endswith("Settings") for base in item.bases)
    if item.decorator_list or item.keywords or not plain:
        return None
    members, rest = {}, []
    for part in item.body:
        named = isinstance(part, ast.AnnAssign) and isinstance(part.target, ast.Name)
        if named and part.value is not None:
            members[part.target.id] = ast.dump(part)
        elif named:
            return None
        else:
            rest.append(ast.dump(part))
    return members, "".join(rest)


def changed_fields(before: str | None, after: str) -> set[str] | None:
    """The data members a change adds to or alters in plain classes, when it does nothing
    else at the top level and no function of the file refers to them; else None."""
    try:
        old, new = ast.parse(before or ""), ast.parse(after)
    except (SyntaxError, ValueError, RecursionError):
        return None
    if before is None or len(old.body) != len(new.body):
        return None
    found: set[str] = set()
    for was, now in zip(old.body, new.body):
        if ast.dump(was) == ast.dump(now):
            continue
        if not (isinstance(was, ast.ClassDef) and isinstance(now, ast.ClassDef)):
            return None
        first, second = _members(was), _members(now)
        if first is None or second is None or first[1] != second[1] or was.name != now.name:
            return None
        if first[0].keys() - second[0].keys():
            return None  # A member was removed: whatever still reads it breaks.
        found |= {name for name, text in second[0].items() if first[0].get(name) != text}
    inside = {
        node.attr
        for function in ast.walk(new)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute)
    }
    return found if found and not found & inside else None


class Uses:
    """Which names of one file another file takes, read from the map and the user's source."""

    def __init__(self, replay: reach_report.Replay):
        self.replay = replay
        nodes = {node.id: node for node in replay.graph.nodes}
        self.symbols: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
        self.lines: dict[tuple[str, str], set[int]] = collections.defaultdict(set)
        self.loose: set[tuple[str, str]] = set()
        for edge in replay.graph.edges:
            user, target = nodes.get(edge.source), nodes.get(edge.target)
            if user is None or target is None or edge.resolution == "unresolved":
                continue
            if user.path == target.path:
                continue
            pair = (user.path, target.path)
            if target.kind == "module" and edge.kind == "imports":
                self.lines[pair].add(edge.line)
            elif target.kind == "module":
                self.loose.add(pair)  # Used as a whole somewhere: through an alias, or not.
            else:
                name = target.data.get("qualified_name", target.name)
                self.symbols[pair].add(name.split(".")[0])
        self.texts: dict[str, str] | None = None
        self.trees: dict[str, ast.Module | None] = {}
        self.shapes: dict[str, Shape | None] = {}
        self.known: dict[tuple[str, str], tuple[set[str] | None, set[str]]] = {}

    def tree(self, path: str) -> ast.Module | None:
        if path not in self.trees:
            try:
                self.trees[path] = ast.parse((self.replay.repo / path).read_bytes())
            except (OSError, SyntaxError, ValueError, RecursionError):
                self.trees[path] = None
        return self.trees[path]

    def shape(self, path: str) -> Shape | None:
        if path not in self.shapes:
            try:
                source = (self.replay.repo / path).read_text()
                self.shapes[path] = Shape(source, path in self.replay.whole)
            except (OSError, SyntaxError, ValueError, RecursionError):
                self.shapes[path] = None
        return self.shapes[path]

    def taken(self, user: str, target: str) -> tuple[set[str] | None, set[str]]:
        """The names of `target` that `user` takes (None when it cannot be told), and the
        names in `user` that stand for the target file as a whole."""
        pair = (user, target)
        if pair not in self.known:
            self.known[pair] = self._taken(user, target)
        return self.known[pair]

    def _taken(self, user: str, target: str) -> tuple[set[str] | None, set[str]]:
        names = set(self.symbols.get((user, target), ()))
        lines = self.lines.get((user, target), set())
        if not lines:  # No import statement names the file: only what the map resolved.
            return (None if (user, target) in self.loose else names), set()
        tree = self.tree(user) if user.endswith(".py") else None
        if tree is None:
            return None, set()
        stem = posixpath.basename(target).removesuffix(".py")
        stem = posixpath.basename(posixpath.dirname(target)) if stem == "__init__" else stem
        aliases: set[str] = set()  # Names in the user that stand for the target file itself.
        explained = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            covered = lines & set(range(node.lineno, (node.end_lineno or node.lineno) + 1))
            if not covered:
                continue
            whole = [alias for alias in node.names if alias.name.split(".")[-1] == stem]
            aliases |= {alias.asname or alias.name for alias in whole}
            if isinstance(node, ast.ImportFrom):
                if any(alias.name == "*" for alias in node.names):
                    return None, set()
                # Any other name on this line may come from the target, also through a
                # package that passes it on.
                names |= {alias.name for alias in node.names if alias not in whole}
            elif not whole:
                return None, set()
            explained |= covered
        if explained != lines:
            return None, set()  # Reached some other way than by an import statement.
        for alias in aliases:
            found = self._attributes(tree, alias)
            if found is None:
                return None, aliases
            names |= found
        return names, aliases

    @staticmethod
    def _attributes(tree: ast.Module, alias: str) -> set[str] | None:
        """The attributes read from a module alias, or None when it is used any other way."""
        dotted = alias.split(".")
        found, inside = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                chain, value = [node.attr], node.value
                while isinstance(value, ast.Attribute):
                    chain.append(value.attr)
                    value = value.value
                if isinstance(value, ast.Name):
                    chain.append(value.id)
                    chain.reverse()
                    if chain[: len(dotted)] == dotted and len(chain) > len(dotted):
                        found.add(chain[len(dotted)])
                        inside.add(id(value))
        for node in ast.walk(tree):
            bare = isinstance(node, ast.Name) and node.id == dotted[0] and id(node) not in inside
            if bare and isinstance(node.ctx, ast.Load):
                return None
        return found

    def mentioning(self, base: Any, path: str, members: set[str]) -> set[str]:
        """Tests of the files that mention these members by name: as an attribute or as a
        quoted word in code, or anywhere in a file that tests read as data."""
        if self.texts is None:
            self.texts = {}
            for name in self.replay.files:
                try:
                    self.texts[name] = (self.replay.repo / name).read_text()
                except (OSError, UnicodeDecodeError):
                    continue
        words = "|".join(re.escape(member) for member in sorted(members))
        code = re.compile(rf"(?:\.|[\"'])(?:{words})\b")
        data = re.compile(rf"\b(?:{words})\b")
        reads = base.reads or {
            "opened": {},
            "through": {"opened": {}},
            "by_modules": {"opened": {}},
        }
        found: set[str] = set()
        for name, text in self.texts.items():
            if name == path or not (code if name.endswith(".py") else data).search(text):
                continue
            if name.endswith(".py"):
                tests = base._direct(name) if name in base.modules else None
                found |= tests if tests is not None else base.consumers(name)[0]
            found |= base.numbered(reads["opened"].get(name, ()))
            for reader in reads["through"]["opened"].get(name, ()):
                found |= base.executed(reader)
            for module in reads["by_modules"]["opened"].get(name, ()):
                if module in base.modules:
                    found |= (base._direct(module) or set()) | base.consumers(module)[0]
        return found

    def passed_on(self, user: str, target: str, changed: set[str]) -> set[str] | None:
        """The names of `user` that a change to these names of `target` reaches when `user`
        is imported; None when all of them might be."""
        taken, aliases = self.taken(user, target)
        shape, tree = self.shape(user), self.tree(user)
        if taken is None or shape is None or tree is None:
            return None
        local = {alias.split(".")[0] for alias in aliases}  # Whatever refers to the file.
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                local |= {a.asname or a.name for a in node.names if a.name in changed}
        return shape.carried(local & shape.names.keys())


def consumers_by_names(uses: Uses, base: Any, path: str, names: set[str]) -> tuple[set[str], Any]:
    """`_Selection.consumers`, following only the files that take one of the changed names,
    and through a file that passes names on at import, only those names."""
    found: set[str] = set()
    lost = None
    reached: dict[str, set[str] | None] = {path: names}  # Per file, its names a change reaches.
    counted: set[str] = set()
    queue = collections.deque([path])
    while queue:
        current = queue.popleft()
        changed = reached[current]
        for user in base.users.get(current, ()):
            taken = None if changed is None else uses.taken(user, current)[0]
            if user == path or (changed is not None and taken is not None and not taken & changed):
                continue  # It takes nothing that changed.
            tests = base._direct(user)
            if user not in counted:
                counted.add(user)
                found |= tests or set()
                special = posixpath.basename(user) in {"__init__.py", "__main__.py"}
                if tests is None and not special and base._orphan(user):
                    lost = lost or user
            if not (tests is None or user in base.importing[current]):
                continue  # Its own tests stand for it.
            onward = None if changed is None else uses.passed_on(user, current, changed)
            known = reached.get(user, set())
            if user in reached and (known is None or (onward is not None and onward <= known)):
                continue  # Nothing more to follow from here.
            reached[user] = None if onward is None or known is None else known | onward
            if reached[user] is None or reached[user]:
                queue.append(user)
    return found, lost


class Trial:
    """Past changes replayed with the trial rule in place of `_Selection.consumers`."""

    def __init__(self, replay: reach_report.Replay):
        self.replay, self.uses = replay, Uses(replay)
        self.narrow: dict[str, set[str]] = {}
        self.members: dict[str, set[str]] = {}
        self.outcomes: collections.Counter[str] = collections.Counter()
        original = selection._Selection.consumers
        whole_file = selection._Selection.source
        trial = self

        def source(self, path, read):
            members = trial.members.get(path)
            if members is None:
                return whole_file(self, path, read)
            tests = trial.uses.mentioning(self, path, members)
            self.add(path, "tests of the files that mention the changed members", tests)

        def consumers(self, path):
            names = trial.narrow.get(path)
            return (
                original(self, path)
                if names is None
                else consumers_by_names(trial.uses, self, path, names)
            )

        self._patched, self._original = (consumers, source), (original, whole_file)

    def __enter__(self) -> Trial:
        selection._Selection.consumers, selection._Selection.source = self._patched  # type: ignore[method-assign]
        return self

    def __exit__(self, *_: Any) -> None:
        selection._Selection.consumers, selection._Selection.source = self._original  # type: ignore[method-assign]

    def names_for(self, path: str, before: str | None, after: str, strict: bool) -> set[str] | str:
        return changed_names(before, after, path in self.replay.whole, strict)

    def pick(
        self,
        files: list,
        sources: dict[str, tuple],
        strict: bool,
        fields: bool = False,
        **what_if: Any,
    ) -> dict:
        """The selection for one past change; `sources` holds each changed file's text before
        and after it."""
        self.narrow, self.members = {}, {}
        for kind, path, same in files:
            top_level = kind != "A" and not same and path in sources and path in self.replay.files
            helper = self.replay.test_like(path)  # A helper's own tests are found by import.
            if top_level and not helper and path in self.replay.record["imports"]:
                members = changed_fields(*sources[path]) if fields else None
                names = self.names_for(path, *sources[path], strict)
                if members is not None:
                    self.members[path] = members
                    self.outcomes["followed by member"] += 1
                elif isinstance(names, str):
                    self.outcomes[f"today's rule: {names}"] += 1
                else:
                    self.narrow[path] = names
                    self.outcomes["followed by name"] += 1
        try:
            return self.replay.pick(files, **what_if)
        finally:
            self.narrow, self.members = {}, {}


def sources_of(replay: reach_report.Replay, change: dict[str, Any]) -> dict[str, tuple]:
    """Text before and after a past change, for each Python file whose top level it changed."""
    found = {}
    for kind, path, same in change["files"]:
        if kind == "A" or same or not path.endswith(".py") or path not in replay.files:
            continue
        texts = []
        for revision in (change["parent"], change["commit"]):
            try:
                texts.append(reach_report.git(replay.repo, "show", f"{revision}:{path}"))
            except subprocess.CalledProcessError:
                texts.append(None)
        if texts[1] is not None:
            found[path] = (texts[0], texts[1])
    return found


def measure(repo: Path, record: dict, history: str, limit: int) -> dict[str, Any]:
    replay = reach_report.Replay(repo, record)
    changes = reach_report.past_changes(replay, history, limit)
    rows: dict[str, list[float]] = collections.defaultdict(list)
    outcomes = {}
    left_out = 0
    with Trial(replay) as trial:
        texts = [sources_of(replay, change) for change in changes]
        for seen in (False, True):
            label = " (always-run tests attributed)" if seen else ""
            for change in changes:
                files = change["files"]
                rows["today" + label].append(replay.pick(files, attributed=seen)["time_share"])
                bound_ = replay.pick(files, attributed=seen, all_local=True)["time_share"]
                rows["every top-level change stays local" + label].append(bound_)
            for strict, fields in ((True, False), (False, False), (True, True)):
                trial.outcomes.clear()
                name = "by name, strict" if strict else "by name, lenient"
                name += (" and by field" if fields else "") + label
                for change, sources in zip(changes, texts):
                    picked = trial.pick(change["files"], sources, strict, fields, attributed=seen)
                    rows[name].append(picked["time_share"])
                    needed = replay.needed(change["files"])
                    left_out += bool(needed - set(picked["test_files"]))
                outcomes[name] = dict(trial.outcomes.most_common())
    return {
        "changes": len(changes),
        "variants": {name: reach_report.summary(values) for name, values in rows.items()},
        "outcomes": outcomes,
        "left_out": left_out,
        "rows": rows,
    }


def render(found: dict[str, Any]) -> str:
    lines = [f"{found['changes']} past changes replayed", ""]
    lines += reach_report.table(
        ["Rule", "Typical", "Mean", "95% or more", "Under 20%"],
        [
            [
                name,
                f"{row['median']:.1%}",
                f"{row['mean']:.1%}",
                str(row["nearly_all"]),
                str(row["under_fifth"]),
            ]
            for name, row in found["variants"].items()
        ],
    )
    for name, outcomes in found["outcomes"].items():
        lines += ["", f"Top-level changes to recorded files, {name}:"]
        lines += [f"  {count:4}  {outcome}" for outcome, count in outcomes.items()]
    lines += ["", f"Changes where a needed test was left out: {found['left_out']}"]
    return "\n".join(lines)


def main(arguments: list[str] | None = None) -> dict[str, Any]:
    options = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    options.add_argument("clone", type=Path)
    options.add_argument("--suite", required=True)
    options.add_argument("--record", type=Path)
    options.add_argument("--history", choices=("pulls", "commits"), default="pulls")
    options.add_argument("--limit", type=int, default=70)
    options.add_argument("--json", type=Path)
    args = options.parse_args(arguments)
    repo = args.clone.resolve()
    saved = args.record or repo / ".anaxigraph-v2" / "test-records" / f"{args.suite}.json"
    started = time.monotonic()
    with reach_report.remembered_users():
        found = measure(repo, json.loads(saved.read_text()), args.history, args.limit)
    print(render(found))
    print(f"\n{time.monotonic() - started:.0f} seconds", file=sys.stderr)
    if args.json:
        args.json.write_text(json.dumps(found, indent=1))
    return found


if __name__ == "__main__":
    main()
