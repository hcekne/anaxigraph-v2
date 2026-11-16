"""Pick the tests a checkout needs from a record of which test executed which file.

The record is compared with the checkout itself, not with a base branch: every file that
differs from the recorded run counts, so the selection widens by itself as the record ages
and a refreshed record narrows it again. Nothing is selected on a guess: when a changed input
cannot be attributed to tests, the whole suite is selected and the reason is given.

Executed lines cannot show a test that only reads a module-level value, so a change to what
runs at import time also selects the tests of the modules that import it.
"""

from __future__ import annotations

from collections import deque
from pathlib import PurePosixPath
from typing import Any

from anaxigraph.analysis.charter import relations
from anaxigraph.contracts import SOURCE_SUFFIXES, Graph

_PROSE = {".md", ".rst", ".adoc"}


def _prose(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in _PROSE or path.startswith("docs/")


class _Selection:
    def __init__(self, record: dict[str, Any], graph: Graph):
        self.record, self.files = record, graph.snapshot.files
        self.tests: list[str] = record["tests"]
        self.known = set(self.tests)
        self.modules, self.relations = relations(graph)
        self.picked: set[str] = set()
        self.reasons: list[dict[str, Any]] = []
        self.everything: list[str] = []

    def add(self, path: str, rule: str, tests: set[str]) -> None:
        tests = {test for test in tests if test in self.files}
        self.reasons.append({"path": path, "rule": rule, "tests": len(tests - self.picked)})
        self.picked |= tests

    def executed(self, path: str) -> set[str]:
        return {self.tests[index] for index in self.record["executed"].get(path, ())}

    def _direct(self, path: str) -> set[str] | None:
        """The tests a file stands for by itself, or None when its importers must be asked."""
        if path in self.known or PurePosixPath(path).stem.startswith("test_"):
            return {path}
        if PurePosixPath(path).name == "conftest.py":
            folder = str(PurePosixPath(path).parent) + "/"
            return {test for test in self.known if test.startswith(folder) or folder == "./"}
        if self.modules[path].data.get("is_test"):
            return None  # A helper beside the tests: every test file that reaches it.
        return self.executed(path) or None  # A module no test executed passes the question on.

    def consumers(self, path: str) -> tuple[set[str], bool]:
        """Tests reached through the modules that import a file, and whether any import it."""
        found: set[str] = set()
        seen, queue = {path}, deque([path])
        while queue:
            for user in self.relations.get(queue.popleft(), {}).get("consumers", ()):
                if user not in seen:
                    seen.add(user)
                    tests = self._direct(user)
                    if tests is None:
                        queue.append(user)
                    else:
                        found |= tests
        return found, len(seen) > 1

    def _test_files(self, path: str) -> set[str]:
        tests = self._direct(path) if path in self.modules or path in self.known else {path}
        return self.consumers(path)[0] if tests is None else tests

    def source(self, path: str) -> None:
        """A changed source file: what the recorded suite ran of it, and who imports it."""
        recorded = self.record["imports"].get(path)
        node = self.modules.get(path)
        if not path.endswith((".py", ".pyi")):
            self.add(path, "another language: this suite cannot load it", set())
            return
        if path in self.record["files"] and recorded is None and path in self.files:
            self.add(path, "not loaded by the recorded suite", set())
            return
        self.add(path, "tests that executed it", self.executed(path))
        current = node.data.get("import_fingerprint") if node is not None else None
        if recorded is not None and recorded == current:
            return
        tests, reached = self.consumers(path)
        if reached or path not in self.files:
            self.add(path, "import-time code changed: tests of the modules that import it", tests)
        else:
            self.everything.append(f"{path}: new or changed at import time, and nothing imports it")

    def change(self, path: str) -> None:
        node = self.modules.get(path)
        if _prose(path):
            return
        if path in self.known or (node is not None and node.data.get("is_test")):
            self.add(path, "a changed test or test helper", self._test_files(path))
        elif path.endswith(SOURCE_SUFFIXES):
            self.source(path)
        else:
            self.everything.append(f"{path}: an input the record cannot attribute to tests")


def _unattributed(selection: _Selection, tests: list[str]) -> None:
    """A test that executed no recorded source reads something else: configuration, data,
    another project. Nothing says what, so it runs whenever anything may have changed."""
    executed = selection.record["executed"].values()
    attributed = {selection.tests[index] for found in executed for index in found}
    selection.add("(anything)", "tests that executed no recorded source", set(tests) - attributed)


def _changed(record: dict[str, Any], graph: Graph) -> list[str]:
    """Every file in the suite's scope that differs from the recorded run."""
    scope, recorded = tuple(record["scope"]), record["files"]
    current = {p: found for p, found in graph.snapshot.files.items() if p.startswith(scope)}
    paths = recorded.keys() | current.keys()
    return sorted(path for path in paths if recorded.get(path) != current.get(path))


def select_tests(record: dict[str, Any], graph: Graph) -> dict[str, Any]:
    """The test files of one recorded suite that the current checkout needs."""
    selection = _Selection(record, graph)
    changed = _changed(record, graph)
    for path in changed:
        selection.change(path)
    tests = [test for test in selection.tests if test in graph.snapshot.files]
    if changed or graph.snapshot.dirty or record.get("revision") != graph.snapshot.revision:
        _unattributed(selection, tests)
    picked = sorted(tests if selection.everything else selection.picked)
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
        "assurance": "Selected from recorded execution and static imports. Run the whole suite "
        "on the main branch: a test that depends on a file without executing or importing it "
        "is not seen here.",
    }
