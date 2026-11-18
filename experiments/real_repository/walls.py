"""Where are the walls? The parts of a checkout, the side each one shows, and the reach-arounds.

A part is a folder of source files. Its public side, in fact, is what other parts use of it
today: its doors. A reach-around is one part using what another keeps private: a name with
a leading underscore, or one its module leaves out of `__all__`. Read from the map alone:
nothing is executed, and no record of a test run is needed.

    uv run python experiments/real_repository/walls.py /path/to/checkout --scope src/
    ... --base main                  what the checkout adds since that revision: new
                                     reach-arounds (exit 1 when there are any) and new doors
    ... --write-known walls.json     write today's reach-arounds and doors down
    ... --known walls.json           the same comparison against that file

`--front-doors` also counts a use that goes past a package whose `__init__` declares
`__all__`. With `--tach tach.toml` the parts are the modules that file declares, and a use of
something its interfaces do not expose is a reach-around too.
"""

from __future__ import annotations

import argparse
import collections
import json
import posixpath
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from anaxigraph.indexing import index

FORMAT = "anaxigraph-walls-v1"
USES = {"imports", "calls", "references", "inherits"}


def private(name: str) -> bool:
    """A leading underscore on any part of a dotted name, other than a double-underscore
    name such as `__init__`."""
    return any(
        part.startswith("_") and not (part.startswith("__") and part.endswith("__"))
        for part in name.split(".")
    )


class Declared:
    """Parts and public sides as a tach.toml declares them."""

    def __init__(self, file: Path):
        found = tomllib.loads(file.read_text())
        roots = found.get("source_roots", ["."])
        self.modules: dict[str, str] = {}  # Folder, or file without its suffix -> module.
        for module in found.get("modules", []):
            for root in roots:
                prefix = posixpath.normpath(posixpath.join(root, *module["path"].split(".")))
                self.modules[prefix] = module["path"]
        self.exposed: dict[str, list[re.Pattern]] = collections.defaultdict(list)
        for interface in found.get("interfaces", []):
            patterns = [re.compile(pattern) for pattern in interface.get("expose", [])]
            for module in interface.get("from", list(self.modules.values())):
                self.exposed[module] += patterns

    def part(self, path: str) -> str | None:
        folder = path.removesuffix(".py")
        while folder and folder not in self.modules:
            folder = posixpath.dirname(folder)
        return self.modules.get(folder)

    def exposes(self, part: str, path: str, name: str) -> bool:
        """Whether a declared interface of the part lets this member out. A part without
        one lets everything out."""
        patterns = self.exposed.get(part)
        if not patterns:
            return True
        prefix = next(folder for folder, module in self.modules.items() if module == part)
        inside = path.removesuffix(".py").removeprefix(prefix).strip("/").replace("/", ".")
        inside = inside.removesuffix("__init__").strip(".")
        member = ".".join(piece for piece in (inside, name) if piece)
        return any(pattern.fullmatch(member) for pattern in patterns)


class Walls:
    """Every use of one part by another in one checkout."""

    def __init__(
        self,
        repo: Path,
        scope: tuple[str, ...],
        declared: Declared | None = None,
        front_doors: bool = False,
    ):
        graph = index(repo)
        self.declared = declared
        nodes = {node.id: node for node in graph.nodes}
        modules = {node.path: node for node in graph.nodes if node.kind == "module"}
        self.sources = {
            path
            for path, node in modules.items()
            if path.startswith(scope) and not node.data.get("is_test")
        }
        self.part = {path: self._part(path) for path in self.sources}
        self.front = self._front_doors(modules) if front_doors else {}
        self.uses: dict[str, dict[str, Any]] = {}
        for edge in graph.edges:
            user, target = nodes.get(edge.source), nodes.get(edge.target)
            if user is None or target is None or edge.resolution != "resolved":
                continue
            if edge.kind not in USES or not {user.path, target.path} <= self.sources:
                continue
            here, there = self.part[user.path], self.part[target.path]
            name = "" if target.kind == "module" else target.data.get("qualified_name", target.name)
            door = f"{target.path}#{name}" if name else target.path
            if here == there or here is None or there is None:
                continue
            self.uses.setdefault(
                f"{user.path} -> {door}",  # Without the line: moving code makes nothing new.
                {
                    "user": user.path,
                    "line": edge.line,
                    "from_part": here,
                    "part": there,
                    "door": door,
                    "why": self._why(there, target.path, name, target),
                },
            )

    def _part(self, path: str) -> str | None:
        if self.declared is not None:
            return self.declared.part(path)
        return posixpath.dirname(path) + "/"

    def _front_doors(self, modules: dict[str, Any]) -> dict[str, set[str]]:
        """Per package whose `__init__` declares `__all__`: those names."""
        found = {}
        for path, node in modules.items():
            surface = node.data.get("public_surface", {})
            listed = surface.get("basis") == "literal-__all__" and not surface.get("truncated")
            if posixpath.basename(path) == "__init__.py" and path in self.sources and listed:
                found[posixpath.dirname(path) + "/"] = set(surface["names"])
        return found

    def _why(self, part: str, path: str, name: str, target: Any) -> str | None:
        """Why a use from another part is a reach-around, or None when it is not one."""
        stem = posixpath.basename(path).removesuffix(".py")
        if private(name) or private(stem):
            return "private name"
        if target.data.get("public") is False:
            return "left out of __all__"
        if self.declared is not None and not self.declared.exposes(part, path, name):
            return "not in the declared interface"
        exported = self.front.get(part)
        if exported is None or not name or posixpath.basename(path) == "__init__.py":
            return None  # A file used as a whole says nothing of the names taken from it.
        return None if name.split(".")[0] in exported else "past the package's __all__"

    def reach_arounds(self) -> dict[str, dict[str, Any]]:
        return {key: use for key, use in sorted(self.uses.items()) if use["why"]}

    def doors(self) -> set[str]:
        return {use["door"] for use in self.uses.values()}

    def parts(self) -> list[dict[str, Any]]:
        files: collections.Counter[str] = collections.Counter(
            part for part in self.part.values() if part
        )
        doors: dict[str, set] = collections.defaultdict(set)
        users: dict[str, set] = collections.defaultdict(set)
        around: collections.Counter[str] = collections.Counter()
        for use in self.uses.values():
            doors[use["part"]].add(use["door"])
            users[use["part"]].add(use["from_part"])
            around[use["part"]] += bool(use["why"])
        rows = []
        for part, count in files.items():
            rows.append(
                {
                    "part": part,
                    "files": count,
                    "doors": len(doors[part]),
                    "files_with_doors": len({door.partition("#")[0] for door in doors[part]}),
                    "used_by_parts": len(users[part]),
                    "reach_arounds": around[part],
                }
            )
        return sorted(rows, key=lambda row: (-row["doors"], row["part"]))


def at_revision(repo: Path, revision: str, folder: Path) -> Path:
    """The checkout's files as they were at a revision, unpacked for reading only."""
    packed = subprocess.run(
        ["git", "-C", str(repo), "archive", revision], capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(folder)], input=packed, check=True)
    return folder


def compare(walls: Walls, before: dict[str, Any]) -> dict[str, Any]:
    """What the checkout adds to a known state: reach-arounds, and doors that nothing outside
    their part used before."""
    around, doors = walls.reach_arounds(), walls.doors()
    known_around, known_doors = set(before["reach_arounds"]), set(before["doors"])
    opened: dict[str, list[str]] = collections.defaultdict(list)
    for key, use in sorted(walls.uses.items()):
        if use["door"] not in known_doors and not use["why"]:
            opened[use["door"]].append(use["user"])
    return {
        "new": [use for key, use in around.items() if key not in known_around],
        "gone": sorted(known_around - around.keys()),
        "new_doors": dict(opened),
        "closed_doors": len(known_doors - doors),
    }


def render(result: dict[str, Any]) -> str:
    parts, around = result["parts"], result["reach_arounds"]
    lines = [
        f"{len(parts)} parts; {result['uses']} uses of one part by another, through "
        f"{result['doors']} doors; {len(around)} reach-arounds",
        "",
        f"{'Part':46}  {'Files':>5}  {'Doors: names others use':27}  {'Used by':>7}  Reach-arounds",
    ]
    for row in parts[:25]:
        doors = f"{row['doors']} in {row['files_with_doors']} of its files"
        lines.append(
            f"{row['part'][:46]:46}  {row['files']:5}  {doors:27}  "
            f"{row['used_by_parts']:>3} parts  {row['reach_arounds']}"
        )
    if len(parts) > 25:
        lines.append(f"... and {len(parts) - 25} more parts")
    shown = result["new"] if "new" in result else list(around.values())
    lines += ["", f"{'New reach-arounds' if 'new' in result else 'Reach-arounds'}: {len(shown)}"]
    for use in shown[:40]:
        lines.append(f"  {use['user']}:{use['line']} uses {use['door']} ({use['why']})")
    if len(shown) > 40:
        lines.append(f"  ... and {len(shown) - 40} more")
    if "new" in result:
        opened = result["new_doors"]
        lines += [f"Reach-arounds gone: {len(result['gone'])}", "", f"New doors: {len(opened)}"]
        for door, users in list(opened.items())[:40]:
            lines.append(f"  {door}, first used from outside its part by {', '.join(users[:3])}")
        lines.append(f"Doors closed: {result['closed_doors']}")
    return "\n".join(lines)


def main(arguments: list[str] | None = None) -> int:
    options = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    options.add_argument("checkout", type=Path)
    options.add_argument("--scope", nargs="+", default=[""], help="Folders to look at")
    options.add_argument("--tach", type=Path, help="A tach.toml that declares the parts")
    options.add_argument("--front-doors", action="store_true", help="Count uses past __all__")
    options.add_argument("--base", help="A git revision to compare the checkout with")
    options.add_argument("--known", type=Path, help="A written state to compare with")
    options.add_argument("--write-known", type=Path, help="Write today's state here")
    options.add_argument("--json", type=Path, help="Write every row here")
    args = options.parse_args(arguments)
    scope = tuple(folder.rstrip("/") + "/" if folder else "" for folder in args.scope)
    declared = Declared(args.tach) if args.tach else None
    repo = args.checkout.resolve()
    walls = Walls(repo, scope, declared, args.front_doors)
    state = {"reach_arounds": sorted(walls.reach_arounds()), "doors": sorted(walls.doors())}
    result: dict[str, Any] = {
        "parts": walls.parts(),
        "uses": len(walls.uses),
        "doors": len(state["doors"]),
        "reach_arounds": walls.reach_arounds(),
    }
    if args.base:
        with tempfile.TemporaryDirectory() as folder:
            before = Walls(
                at_revision(repo, args.base, Path(folder)), scope, declared, args.front_doors
            )
            known = {"reach_arounds": list(before.reach_arounds()), "doors": list(before.doors())}
            result |= compare(walls, known)
    elif args.known:
        result |= compare(walls, json.loads(args.known.read_text()))
    if args.write_known:
        args.write_known.write_text(json.dumps({"format": FORMAT} | state, indent=1) + "\n")
    print(render(result))
    if args.json:
        args.json.write_text(json.dumps(result, indent=1))
    return 1 if result.get("new") else 0


if __name__ == "__main__":
    sys.exit(main())
