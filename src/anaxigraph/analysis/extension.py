"""Open-ended families that grow by editing shared files, and what that costs safe CI.

A family is a set of sibling modules built on one shared contract module. A registrar is a
module that names most members: a composition root, a hand-written registry, a test that lists
every member, or a file such as a test-selection map that names members by path or module name.
Each new member then edits the registrar, and every check that imports the registrar is in scope
for that edit. Discovery (package scanning, entry points) leaves no static registrar, so an open
family produces no finding here.
"""

from __future__ import annotations

import math
import threading
import weakref
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from anaxigraph.analysis.charter import relations
from anaxigraph.contracts import Graph

_SKIP = {"__init__.py", "__main__.py", "conftest.py"}
_MEMBER_CONSUMERS = 3
_TEXT_LIMIT = 1_000_000
_CACHE: tuple[weakref.ReferenceType[Graph], dict[str, Any]] | None = None
_LOCK = threading.Lock()
LIMITATION = (
    "Families are found from static imports; files that name members as strings are seen only "
    "when a change edits them. Import reach over-approximates the checks a change can affect."
)


def _directory(path: str) -> str:
    return str(PurePosixPath(path).parent)


def _kin(members: set[str], raw: dict, consumers: dict[str, int]) -> tuple[str, set[str]] | None:
    """The most specific shared dependency of at least two members, and the members using it."""
    counts = Counter(dep for member in members for dep in raw[member]["dependencies"] - members)
    shared = [dep for dep, count in counts.items() if count >= 2]
    if not shared:
        return None
    contract = min(shared, key=lambda dep: (-counts[dep], consumers.get(dep, 0), dep))
    return contract, {member for member in members if contract in raw[member]["dependencies"]}


def _leaf_imports(row: dict, production: set[str], consumers: dict[str, int]) -> dict:
    """Imported leaf-like modules by directory; widely imported modules are infrastructure."""
    by_directory: dict[str, set[str]] = {}
    for dep in row["dependencies"] & production:
        if PurePosixPath(dep).name not in _SKIP and consumers[dep] <= _MEMBER_CONSUMERS:
            by_directory.setdefault(_directory(dep), set()).add(dep)
    return by_directory


def _analyse(graph: Graph) -> dict[str, Any]:
    modules, raw = relations(graph)
    tests = {path for path, node in modules.items() if node.data.get("is_test")}
    production = set(modules) - tests
    consumers = {path: len(raw[path]["consumers"] & production) for path in production}
    families: dict[tuple[str, str], dict[str, Any]] = {}
    for registrar in modules:
        for directory, members in _leaf_imports(raw[registrar], production, consumers).items():
            found = _kin(members, raw, consumers) if len(members) >= 2 else None
            if found is None or len(found[1]) < 2:
                continue
            contract, kin = found
            family = families.setdefault(
                (directory, contract),
                {"directory": directory, "contract": contract, "members": set(), "listing": {}},
            )  # fmt: skip
            family["members"] |= kin
            family["listing"][registrar] = kin
    for family in families.values():
        # A registrar lists most members; plumbing that uses one or two of them is not one.
        need = max(2, math.ceil(len(family["members"]) / 2))
        listing = {path for path, kin in family["listing"].items() if len(kin) >= need}
        listing -= family["members"]
        family["registrars"], family["test_registrars"] = listing - tests, listing & tests
    # Tests that import a few modules are ordinary unit tests unless production also enumerates.
    closed = [family for family in families.values() if family["registrars"]]
    return {"modules": modules, "raw": raw, "tests": tests, "families": closed}


def _state(graph: Graph) -> dict[str, Any]:
    global _CACHE
    with _LOCK:
        if _CACHE is None or _CACHE[0]() is not graph:
            _CACHE = (weakref.ref(graph), _analyse(graph))
        return _CACHE[1]


def check_reach(graph: Graph, paths: set[str] | list[str]) -> dict[str, Any]:
    """Test modules that import any of these paths, directly or transitively.

    A reached conftest.py brings every test module beneath its directory.
    """
    state = _state(graph)
    raw, tests = state["raw"], state["tests"]
    seen, frontier = set(), [path for path in paths if path in raw]
    while frontier:
        path = frontier.pop()
        if path not in seen:
            seen.add(path)
            frontier.extend(raw[path]["consumers"] - seen)
    for conftest in [path for path in seen if PurePosixPath(path).name == "conftest.py"]:
        base = _directory(conftest)
        seen.update(path for path in tests if base == "." or path.startswith(base + "/"))
    reached = sorted(path for path in seen & tests if PurePosixPath(path).name != "conftest.py")
    total = sum(PurePosixPath(path).name != "conftest.py" for path in tests)
    return {
        "test_modules": len(reached),
        "test_modules_total": total,
        "share": round(len(reached) / total, 2) if total else None,
        "examples": reached[:5],
    }


def _view(graph: Graph, family: dict[str, Any]) -> dict[str, Any]:
    registrars = sorted(family["registrars"] | family["test_registrars"])
    return {
        "family": f"{family['directory']}/ on {family['contract']}",
        "directory": family["directory"],
        "contract": family["contract"],
        "members": len(family["members"]),
        "member_examples": sorted(family["members"])[:4],
        "registrars": sorted(family["registrars"])[:6],
        "test_registrars": sorted(family["test_registrars"])[:6],
        "registrar_check_reach": check_reach(graph, registrars),
    }


def closed_families(graph: Graph, near: set[str] | None = None, limit: int = 3) -> list[dict]:
    """Families with static registrars, largest first; optionally only those touching `near`."""
    rows = [
        family
        for family in _state(graph)["families"]
        if near is None
        or near & (family["members"] | family["registrars"] | family["test_registrars"])
        or any(_directory(path) == family["directory"] for path in near)
    ]
    rows.sort(key=lambda item: (-len(item["members"]), item["directory"], item["contract"]))
    return [_view(graph, family) for family in rows[:limit]]


def _names(path: str) -> set[str]:
    stem = path.removesuffix(".py")
    return {path, stem, stem.replace("/", ".")}


def _text(root: Path | None, path: str) -> str:
    target = root / path if root else None
    if target is None or not target.is_file() or target.stat().st_size > _TEXT_LIMIT:
        return ""
    return target.read_text(errors="ignore")


def _named(root: Path | None, path: str, members: set[str]) -> set[str]:
    """Members a file names as a path or module string, as a hand-written map does."""
    text = _text(root, path)
    return {member for member in members if any(name in text for name in _names(member))}


def change_findings(
    graph: Graph,
    added: set[str],
    modified: set[str],
    root: Path | None = None,
    *,
    applied: bool = False,
) -> list[dict[str, Any]]:
    """Members added to a family while editing files that list that family's members.

    With `applied`, the change is already on disk: an edited registrar only counts when it now
    names one of the added members, so plumbing edits that list nothing are not findings.
    """
    state = _state(graph)
    findings = []
    for family in state["families"]:
        joining = sorted(
            path
            for path in added
            if path not in state["tests"]
            and (path in family["members"] or _directory(path) == family["directory"])
            and PurePosixPath(path).name not in _SKIP
        )
        if not joining:
            continue
        listing = family["registrars"] | family["test_registrars"]
        need = max(2, math.ceil(len(family["members"]) / 2))
        edited = sorted(
            path
            for path in modified - family["members"]
            if path in listing or len(_named(root, path, family["members"] - set(joining))) >= need
        )
        if applied:
            edited = [path for path in edited if _lists(state, root, path, set(joining))]
        if edited:
            findings.append(_finding(graph, family, joining, edited))
    return findings


def _lists(state: dict, root: Path | None, path: str, joining: set[str]) -> bool:
    row = state["raw"].get(path)
    return bool(row and row["dependencies"] & joining) or bool(_named(root, path, joining))


def _finding(graph: Graph, family: dict, joining: list[str], edited: list[str]) -> dict:
    view = _view(graph, family)
    reach = check_reach(graph, edited)
    share = f"{reach['test_modules']} of {reach['test_modules_total']} test modules"
    directory = family["directory"]
    return {
        "kind": "extension-point",
        "proposal_id": f"extension:{directory}:{family['contract']}",
        "status": "closed-family",
        "title": f"Adding to {directory}/ edits shared registrars; open the family instead",
        "opportunity": 3,
        "adding": joining[:6],
        "editing_registrars": edited[:6],
        "family": view,
        "ci_cost": f"These registrar edits reach {share} through imports, and a selector that "
        "treats shared wiring as global runs the whole suite. Every later addition repeats that, "
        "and parallel additions conflict in the same files.",
        "thousand_requests": f"After 1,000 more members, would {', '.join(edited[:3])} still be "
        "the right place to list them, and would every addition still run that much CI?",
        "suggestion": (
            f"Before adding this member, make {directory}/ an open extension point: (1) each member "
            f"exports one object implementing the family contract ({family['contract']}) that "
            "declares its capabilities, settings and any routes; (2) one loader discovers members "
            "(for example pkgutil.iter_modules over the package, or entry points), so no existing "
            "file names a member; (3) contract tests parametrize over discovered members, and each "
            "member's own tests live in its own test directory; (4) the CI selector maps a member "
            "file to its own tests plus the contract tests, and only loader or contract changes "
            "select everything. The next member then adds files only."
        ),
        "cost_of_staying_closed": f"Every future member edits {', '.join(edited[:3])} and so "
        f"selects about {reach['test_modules']} test modules instead of its own tests plus the "
        "contract tests. Across 1,000 more members that "
        f"is about {1000 * reach['test_modules']:,} test-module runs and 1,000 edits to the same "
        "shared files.",
        "decision": "adopt (open the family in this change). An explicit registry or selector "
        "map is still a hand-written list, so it is not an open family. To defer or reject, "
        "also give revisit_when: the member count or date at which the family will be opened.",
        "requires_revisit_when": True,
        "limitations": LIMITATION,
    }


def design_brief(graph: Graph, near: set[str]) -> dict[str, Any]:
    """Short, opinionated questions to answer before planning, ahead of the evidence."""
    families = closed_families(graph, near)
    questions = [
        "List the existing files this request will edit. If the next similar request would "
        "edit the same ones, they are an extension point to open now, not a place to append.",
        "Which room owns this, and which tests prove that room on its own? A change inside one "
        "room should select only its tests plus the contract tests of its interface.",
    ]
    for family in families:
        reach = family["registrar_check_reach"]
        questions.append(
            f"{family['directory']}/ has {family['members']} members on {family['contract']} "
            f"registered by editing {', '.join(family['registrars'][:3])} (reaches "
            f"{reach['test_modules']} of {reach['test_modules_total']} test modules). Adding "
            "another the same way repeats that cost: open the family first; assess_plan will "
            "ask for a decision."
        )
    return {"questions": questions, "closed_families": families, "limitations": LIMITATION}
