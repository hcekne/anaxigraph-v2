"""Turn a recorded test run into a test record, without running anything.

The project runs its own suite with one coverage context per test (for pytest:
`--cov --cov-context=test --junitxml=...`). The record says which test files executed which
source files and what every file in scope looked like then, so a later checkout can be
compared against it.
"""

from __future__ import annotations

import posixpath
import sqlite3
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from anaxigraph.contracts import Graph

FORMAT = "anaxigraph-test-record-v1"


def _executions(database: Path) -> tuple[dict[int, str], dict[int, str], set[tuple[int, int]]]:
    try:
        db = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            files = dict(db.execute("SELECT id, path FROM file"))
            contexts = dict(db.execute("SELECT id, context FROM context"))
            pairs = set(db.execute("SELECT DISTINCT file_id, context_id FROM line_bits"))
            pairs |= set(db.execute("SELECT DISTINCT file_id, context_id FROM arc"))
        finally:
            db.close()
    except sqlite3.Error as error:
        raise ValueError(f"Not a coverage.py data file: {database}") from error
    return files, contexts, pairs


def _junit(report: Path | None, run_root: str, known: dict[str, str]) -> dict[str, float]:
    """Seconds per test file from a JUnit report, so tests that touch no source are known too."""
    seconds: dict[str, float] = {}
    for case in ElementTree.parse(report).getroot().iter("testcase") if report else ():
        parts = (case.get("classname") or "").split(".")
        for end in range(len(parts), 0, -1):  # The class name follows the module's dotted path.
            path = posixpath.normpath(posixpath.join(run_root, "/".join(parts[:end]) + ".py"))
            if path in known:
                seconds[path] = seconds.get(path, 0.0) + float(case.get("time") or 0)
                break
    return seconds


def _executed(
    database: Path, known: dict[str, str], prefix: str, scope: tuple[str, ...], run_root: str
) -> tuple[dict[str, set[str]], set[str]]:
    """Test files by the source file they executed, and every source the suite loaded."""
    files, contexts, pairs = _executions(database)
    executed: dict[str, set[str]] = {}
    loaded = set()
    for file_id, context_id in pairs:
        source = files[file_id].removeprefix(prefix)
        if source in known and source.startswith(scope):
            loaded.add(source)
            name = contexts[context_id].partition("|")[0].partition("::")[0]
            test = posixpath.normpath(posixpath.join(run_root, name)) if name else ""
            if test in known and test != source:
                executed.setdefault(source, set()).add(test)
    return executed, loaded


def record_from_coverage(
    graph: Graph,
    database: Path,
    *,
    suite: str,
    scope: list[str],
    run_root: str = ".",
    recorded_root: str | None = None,
    junit: Path | None = None,
) -> dict[str, Any]:
    """A record for one suite. `run_root` is the folder the suite ran in, relative to the
    checkout; `recorded_root` is the checkout's path on the machine that recorded the run.
    """
    known = graph.snapshot.files
    prefix = (recorded_root or graph.snapshot.root).rstrip("/") + "/"
    folders = tuple(item.rstrip("/") + "/" for item in scope)
    executed, loaded = _executed(database, known, prefix, folders, run_root)
    seconds = _junit(junit, run_root, known)
    tests = sorted(seconds.keys() | {test for found in executed.values() for test in found})
    position = {test: index for index, test in enumerate(tests)}
    modules = {node.path: node for node in graph.nodes if node.kind == "module"}
    return {
        "format": FORMAT,
        "suite": suite,
        "scope": list(folders),
        "revision": graph.snapshot.revision,
        "files": {path: found for path, found in known.items() if path.startswith(folders)},
        "imports": {
            path: modules[path].data.get("import_fingerprint") for path in loaded if path in modules
        },
        "tests": tests,
        "seconds": [round(seconds.get(test, 0.0), 3) for test in tests],
        "executed": {
            source: sorted(position[test] for test in found) for source, found in executed.items()
        },
    }
