"""Open-ended families that grow by editing shared files, and what that costs safe CI.

A family is a set of sibling modules built on one shared contract module. A registrar is a
module that names several members through static imports: a composition root, a hand-written
registry or a test that lists every member. Each new member then edits the registrar, and every
check that imports the registrar is in scope for that edit. Discovery (package scanning, entry
points) leaves no static registrar, so an open family produces no finding here.
"""

from __future__ import annotations

import threading
import weakref
from collections import Counter
from pathlib import PurePosixPath
from typing import Any

from anaxigraph.analysis.charter import relations
from anaxigraph.contracts import Graph

_SKIP = {"__init__.py", "__main__.py", "conftest.py"}
_MEMBER_CONSUMERS = 3
_CACHE: tuple[weakref.ReferenceType[Graph], dict[str, Any]] | None = None
_LOCK = threading.Lock()
LIMITATION = (
    "Static imports only: families wired through strings, configuration or dynamic imports are "
    "not seen, and import reach over-approximates the checks a change can affect."
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
                {"directory": directory, "contract": contract, "members": set(),
                 "registrars": set(), "test_registrars": set()},
            )  # fmt: skip
            family["members"] |= kin
            family["test_registrars" if registrar in tests else "registrars"].add(registrar)
    for family in families.values():
        family["registrars"] -= family["members"]
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


def change_findings(graph: Graph, added: set[str], modified: set[str]) -> list[dict[str, Any]]:
    """Members added to a family while editing that family's registrars."""
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
        edited = sorted(modified & (family["registrars"] | family["test_registrars"]))
        if joining and edited:
            findings.append(_finding(graph, family, joining, edited))
    return findings


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
        "decision": "adopt (open it in this change), defer (name when) or reject with a reason",
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
