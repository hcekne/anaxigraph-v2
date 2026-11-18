"""Pick the tests a checkout needs from a record of what each test ran and read.

The record is compared with the checkout itself, not with a base branch: every file that
differs from the recorded run counts, so the selection widens by itself as the record ages
and a refreshed record narrows it again. Nothing is selected on a guess: when a changed input
cannot be attributed to tests, the whole suite is selected and the reason is given.

Executed lines cannot show a test that only reads what a module set up when it was imported.
So a change to what runs at import also selects the tests of every module that uses the
file, and the same again for each of those whose own import-time code uses it. Where that
walk ends at a file that nothing imports and no recorded test ran, something other than an
import statement loads it, and the record cannot say for which tests.

A record taken with the reads recorder also knows the files tests read as data, the folders
they scanned, the tests that started another process and the modules the run loaded; without
it, a changed data file selects everything. Code that reads a file once and keeps it serves
later tests without reading again, so a read also counts for every test that ran the code
which did it.
"""

from __future__ import annotations

import copy
import posixpath
import re
from collections import defaultdict, deque
from pathlib import PurePosixPath
from typing import Any

from anaxigraph.contracts import SOURCE_SUFFIXES, Graph

_PROSE = {".md", ".rst", ".adoc"}
_ASSURANCE = {
    True: "Selected from what the recorded run executed and read, and from static imports. "
    "Run the whole suite on the main branch: a test that only checks whether a file exists, "
    "or shares what another test set up, is not seen here.",
    False: "Selected from what the recorded run executed, and from static imports. Run the "
    "whole suite on the main branch: a test that reads a file as data, or starts a process "
    "that does, is not seen here. Record with the reads recorder to cover those.",
}
_USES = {"calls", "references", "inherits"}


def _prose(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in _PROSE or path.startswith("docs/")


def _installs(path: str) -> bool:
    """A dependency list or lock file. The suite is installed from it before any test runs,
    so a test that happens to read it does not stand for what depends on it."""
    name = PurePosixPath(path).name
    listed = ("requirements" in name or "constraints" in name) and name.endswith((".txt", ".in"))
    named = name in {"pyproject.toml", "setup.py", "setup.cfg", "Pipfile"}
    return listed or named or name.endswith(".lock")


def _shared_setup(path: str, ancestors: set[PurePosixPath]) -> bool:
    return path.startswith((".github/workflows/", ".github/actions/")) or (
        _installs(path) and PurePosixPath(path).parent in ancestors
    )


def _users(graph: Graph, inside: set[str]) -> tuple[dict[str, set[str]], Any]:
    """Among the suite's files, those that link to each file, and those among them whose
    import-time code uses it.

    A possible link counts: leaving out an affected test is the costly mistake. Code outside
    functions runs when its module is imported, so what it uses shapes that module for
    everyone who imports it in turn.
    """
    nodes = {node.id: node for node in graph.nodes}
    users: dict[str, set[str]] = defaultdict(set)
    importing: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        source, target = nodes.get(edge.source), nodes.get(edge.target)
        if source is None or target is None or edge.resolution == "unresolved":
            continue
        if source.path == target.path or not {source.path, target.path} <= inside:
            continue
        users[target.path].add(source.path)
        if source.kind != "function" and edge.kind in _USES:
            importing[target.path].add(source.path)
    return users, importing


class _Selection:
    def __init__(self, record: dict[str, Any], graph: Graph):
        self.record, self.files = record, graph.snapshot.files
        self.tests: list[str] = record["tests"]
        self.known = set(self.tests)
        self.modules = {node.path: node for node in graph.nodes if node.kind == "module"}
        self.scope = tuple(record["scope"])
        self.ancestors = {
            parent for folder in self.scope for parent in PurePosixPath(folder).parents
        }
        inside = {path for path in self.modules if path.startswith(self.scope)} | self.known
        self.users, self.importing = _users(graph, inside | record["imports"].keys())
        self.idle = set(record["unloaded"])  # Known not to have been loaded by the run.
        self.reads: dict[str, Any] | None = record["reads"]
        self.picked: set[str] = set()
        self.reasons: list[dict[str, Any]] = []
        self.everything: list[str] = []

    def add(self, path: str, rule: str, tests: set[str]) -> None:
        tests = {test for test in tests if test in self.files}
        self.reasons.append({"path": path, "rule": rule, "tests": len(tests - self.picked)})
        self.picked |= tests

    def numbered(self, numbers: Any) -> set[str]:
        return {self.tests[index] for index in numbers}

    def executed(self, path: str) -> set[str]:
        return self.numbered(self.record["executed"].get(path, ()))

    def _direct(self, path: str) -> set[str] | None:
        """The tests a file stands for by itself, or None when its importers must be asked."""
        stem = PurePosixPath(path).stem
        if path in self.known or stem.startswith("test_") or stem.endswith("_test"):
            return {path}
        if PurePosixPath(path).name == "conftest.py":
            folder = str(PurePosixPath(path).parent) + "/"
            return {test for test in self.known if test.startswith(folder) or folder == "./"}
        if self.modules[path].data.get("is_test") and "test_basis" not in self.modules[path].data:
            return None  # Path-convention helper; source hints cannot erase recorded consumers.
        return self.executed(path) or None  # A module no test executed passes the question on.

    def _orphan(self, path: str) -> bool:
        """Whether something other than an import statement must load a file, if anything
        does: nothing imports it, and the run is not known to have left it alone."""
        return not (self.users.get(path) or path in self.idle)

    def consumers(self, path: str) -> tuple[set[str], str | None]:
        """Tests that can see what a file sets up at import, and a file on the way whose
        tests cannot be named: it uses the file, no recorded test ran it and nothing imports
        it."""
        found: set[str] = set()
        lost = None
        seen, queue = {path}, deque([path])
        while queue:
            current = queue.popleft()
            for user in self.users.get(current, ()):
                if user not in seen:
                    seen.add(user)
                    tests = self._direct(user)
                    found |= tests or set()
                    # A package's __init__.py is loaded with the package, and __main__.py
                    # only as a program: neither is a sign of another way of loading.
                    special = PurePosixPath(user).name in {"__init__.py", "__main__.py"}
                    if tests is None and not special and self._orphan(user):
                        lost = lost or user
                    if tests is None or user in self.importing[current]:
                        queue.append(user)
        return found, lost

    def _helper(self, path: str, read: bool) -> set[str]:
        """The test files a changed test, helper or conftest stands for, and those that
        import from it."""
        own = self._direct(path)
        tests, lost = self.consumers(path)
        if lost:
            self.everything.append(f"{path}: {lost} uses it, and no import leads to that")
        elif own is None and self._orphan(path) and not read:
            self.everything.append(f"{path}: a test helper that nothing imports")
        return (own or set()) | tests

    def _folder(self, path: str) -> str:
        """The nearest folder above a file that was there when the run was recorded."""
        folder = posixpath.dirname(path)
        while folder and not any(known.startswith(folder + "/") for known in self.record["files"]):
            folder = posixpath.dirname(folder)
        return folder

    def _kept(self, path: str, module: str, did: str) -> None:
        """What a module read while it was imported belongs to that module's tests."""
        if not module:
            self.everything.append(f"{path}: read by the test run itself")
        elif module in self.modules:
            own = self._direct(module)
            tests, lost = self.consumers(module)
            self.add(path, f"{module} {did} when imported: its tests", (own or set()) | tests)
            lost = lost or (module if own is None and self._orphan(module) else None)
            if lost:
                self.everything.append(
                    f"{path}: {module} {did} when imported, and no import leads to {lost}"
                )

    def read(self, path: str) -> bool:
        """Select what read a file as data, or scanned the folder it appeared in or left."""
        if self.reads is None:
            return False
        asked = [("opened", path, "read it")]
        if (path in self.files) != (path in self.record["files"]):
            asked.append(("listed", self._folder(path), "scanned its folder"))
        found = False
        for kind, key, did in asked:
            tests = self.numbered(self.reads[kind].get(key, ()))
            for reader in self.reads["through"][kind].get(key, ()):
                tests |= self.executed(reader)  # They may share what that code kept of it.
            modules = self.reads["by_modules"][kind].get(key, ())
            found = found or bool(tests or modules)
            if tests:
                self.add(path, f"tests that {did}, or ran the code that did", tests)
            for module in modules:
                self._kept(path, module, did)
        return found

    def source(self, path: str, read: bool) -> None:
        """A changed Python file: what the recorded suite ran of it, and who uses it."""
        if path in self.idle:
            self.add(path, "not loaded by the recorded suite", set())
            return
        recorded = self.record["imports"].get(path)
        node = self.modules.get(path)
        self.add(path, "tests that executed it", self.executed(path))
        whole = path in self.record["ran_at_import"]  # Its functions ran outside any test.
        key = "semantic_fingerprint" if whole else "import_fingerprint"
        if recorded is not None and node is not None and recorded == node.data.get(key):
            return
        # Without a recorded identity nothing is known of what it does: any change counts.
        what = "import-time code changed" if recorded else "new, or not measured when recorded"
        tests, lost = self.consumers(path)
        self.add(path, f"{what}: tests of the modules that use it", tests)
        if lost:
            self.everything.append(f"{path}: {what}; {lost} uses it, and no import leads to that")
        elif not self.users.get(path) and not read:
            self.everything.append(f"{path}: {what}, and nothing imports it")

    def _starter(self, module: str) -> set[str]:
        """An unmeasured module that started a process stands for the tests of what uses it."""
        found, lost = self.consumers(module)
        inside = module in self.modules and module.startswith(self.scope)
        if lost or not inside:
            self.everything.append(f"Process-starting code was not measured: {module}")
        return found | ((self._direct(module) or set()) if inside else set())

    def _outside(self, path: str) -> bool:
        """Whether only reads count for a path; unmeasured code that ran there picks everything."""
        outside = not path.startswith(self.scope) and path not in self.record["imports"]
        if outside and path in self.record.get("unmeasured", ()):
            self.everything.append(f"{path}: loaded by the recorded run, but not measured")
        return outside

    def change(self, path: str) -> None:
        node = self.modules.get(path)
        read = self.read(path)
        python = path.endswith(".py")
        if _shared_setup(path, self.ancestors):
            self.everything.append(f"{path}: shared installation or CI setup changed")
            return
        if self._outside(path):
            return
        if python and path not in self.files:
            self.everything.append(f"{path}: removed, and what still imports it is not recorded")
        elif _installs(path):
            self.everything.append(f"{path}: the suite is installed from it")
        elif path in self.known or (python and node is not None and node.data.get("is_test")):
            self.add(path, "a changed test or test helper", self._helper(path, read))
        elif python:
            self.source(path, read)
        elif _prose(path):
            return
        elif not read and (self.reads is None or not path.endswith(SOURCE_SUFFIXES)):
            # Source the suite cannot import is an input only to a test that read it, and a
            # record with reads has just answered that.
            self.everything.append(f"{path}: an input the record cannot attribute to tests")


def _unseen(selection: _Selection, tests: list[str]) -> None:
    """Tests whose inputs the record cannot name, so they run whenever anything changed.

    One that executed no recorded source and read no recorded file depends on something else:
    configuration, another project. One that started another process depends on whatever
    that process loaded.
    """
    record, reads = selection.record, selection.reads or {}
    seen = {index for found in record["executed"].values() for index in found}
    for kind in ("opened", "listed"):
        seen |= {index for found in reads.get(kind, {}).values() for index in found}
    selection.add(
        "(anything)", "tests with no recorded input", set(tests) - selection.numbered(seen)
    )
    started = selection.numbered(reads.get("started", ()))
    selection.add("(anything)", "tests that started another process", started)
    for module in reads.get("process_modules", ()):
        measured = module in record["executed"]
        found = selection.executed(module) if measured else selection._starter(module)
        selection.add(module, "tests of code that started another process", found)
    if reads.get("unattributed_processes"):
        selection.everything.append(
            "Unobserved Python startup hooks require full checks"
            if "@startup" in reads["unattributed_processes"]
            else "A process started outside an attributable test"
        )


def _changed(record: dict[str, Any], graph: Graph) -> list[str]:
    """Compare every mapped input; unbound legacy scope cannot rule out shared setup."""
    recorded, current = record["files"], graph.snapshot.files
    paths = recorded.keys() | current.keys()
    return sorted(path for path in paths if recorded.get(path) != current.get(path))


def _recording_limit(record: dict[str, Any]) -> str | None:
    provenance = record.get("provenance", {})
    if any(re.search(r"(^/|(^|/)\.{1,2}/|//)", folder) for folder in record["scope"]):
        return "Recording scope is not normalized; record the suite again"
    if provenance.get("status") != "verified":
        return "Recording source/run is unverified; repeat with --start and the reads recorder"
    if provenance.get("observations") != "anaxigraph-reads-v3":
        return "Older observation rules; update the recorder, then record the suite again"
    return None


def select_tests(record: dict[str, Any], graph: Graph) -> dict[str, Any]:
    """The test files of one recorded suite that the current checkout needs."""
    selection = _Selection(record, graph)
    changed = _changed(record, graph)
    for path in changed:
        selection.change(path)
    tests = [test for test in selection.tests if test in graph.snapshot.files]
    if changed or graph.snapshot.dirty or record.get("revision") != graph.snapshot.revision:
        limitation = _recording_limit(record)
        if limitation:
            selection.everything.append(limitation)
        _unseen(selection, tests)
    tests = sorted(selection.picked.union(tests))  # With test files newer than the record.
    picked = tests if selection.everything else sorted(selection.picked)
    seconds = dict(zip(selection.tests, record.get("seconds") or []))
    total = sum(seconds.get(test, 0.0) for test in tests)
    spent = sum(seconds.get(test, 0.0) for test in picked)
    status = "all" if selection.everything else "selected" if picked else "nothing_to_run"
    return {
        "suite": record["suite"],
        "status": status,
        "test_files": picked,
        "selected": len(picked),
        "total": len(tests),
        "time_share": round(spent / total, 3) if total else None,
        "everything_because": selection.everything[:10],
        "because": sorted(selection.reasons, key=lambda item: -item["tests"])[:20],
        "changed_since_record": len(changed),
        "recorded_revision": record.get("revision"),
        "reads_recorded": selection.reads is not None,
        "assurance": _ASSURANCE[selection.reads is not None],
    }


def reach(record: dict[str, Any], graph: Graph, paths: list[str]) -> dict[str, Any]:
    """Test reach: the share of the recorded suite's time a change would pick. For every
    change whatever it touches, and per file for a change inside a function and for one at
    its top level."""
    base = _Selection(record, graph)
    tests = [test for test in base.tests if test in graph.snapshot.files]
    seconds = dict(zip(base.tests, record.get("seconds") or []))
    total = sum(seconds.get(test, 0.0) for test in tests) or 1.0

    def after(path: str | None, imports: dict[str, Any]) -> tuple[float, list[str]]:
        one = copy.copy(base)
        one.record = record | {"imports": imports}
        one.picked, one.reasons, one.everything = set(), [], []
        if path:
            one.change(path)
        _unseen(one, tests)
        spent = sum(seconds.get(test, 0.0) for test in one.picked & set(tests))
        return (1.0 if one.everything else round(spent / total, 3)), one.everything[:3]

    known = record["imports"]
    rows = [
        {
            "path": path,
            "inside_function": after(path, known)[0],
            "top_level": after(path, known | {path: "changed"})[0],
        }
        for path in paths
        if path in record["files"] and (path.startswith(base.scope) or path in known)
    ]
    share, why = after(None, known)
    found = {"suite": record["suite"], "every_change": share, "whole_suite_because": why}
    return found | {"files": rows} if paths else found
