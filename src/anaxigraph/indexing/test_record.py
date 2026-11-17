"""Turn a recorded test run into a test record, without running anything.

The project runs its own suite with one coverage context per test (for pytest:
`--cov --cov-context=test --junitxml=...`), preferably with the reads recorder loaded too.
The record says which test files executed and read which files and what every file looked
like then, so a later checkout can be compared against it.
"""

from __future__ import annotations

import ast
import json
import posixpath
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from anaxigraph.contracts import Graph
from anaxigraph.indexing.python import runs_bodies
from anaxigraph.indexing.test_run import verify

FORMAT = "anaxigraph-test-record-v2"
READS = "anaxigraph-reads-v3"


def _executions(
    database: Path, apart: Callable[[str], bool]
) -> tuple[dict[int, str], dict[int, str], set[tuple[int, int]], dict[int, set[int]]]:
    """Files, contexts, which context ran which file, and per file the lines that ran in the
    contexts `apart` accepts by name."""
    pairs: set[tuple[int, int]] = set()
    lines: dict[int, set[int]] = {}
    try:
        db = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            files = dict(db.execute("SELECT id, path FROM file"))
            contexts = dict(db.execute("SELECT id, context FROM context"))
            outside = {key for key, name in contexts.items() if apart(name)}
            for file_id, context_id, bits in db.execute(
                "SELECT file_id, context_id, numbits FROM line_bits"
            ):
                pairs.add((file_id, context_id))
                if context_id in outside:  # One bit per line number, lowest bit first.
                    lines.setdefault(file_id, set()).update(
                        8 * index + bit
                        for index, byte in enumerate(bits)
                        for bit in range(8)
                        if byte >> bit & 1
                    )
            for file_id, context_id, start, end in db.execute(
                "SELECT file_id, context_id, fromno, tono FROM arc"
            ):
                pairs.add((file_id, context_id))
                if context_id in outside:
                    lines.setdefault(file_id, set()).update(n for n in (start, end) if n > 0)
        finally:
            db.close()
    except sqlite3.Error as error:
        raise ValueError(f"Not a coverage.py data file: {database}") from error
    return files, contexts, pairs, lines


def junit_cases(
    reports: list[Path], run_root: str, known: dict[str, str]
) -> tuple[dict[str, float], dict[str, set[str]]]:
    """Seconds per file and failed testcase identities, from JUnit reports; tests
    that touch no source are known this way too."""
    seconds: dict[str, float] = {}
    failed: dict[str, set[str]] = {}
    for report in reports:
        for case in ElementTree.parse(report).getroot().iter("testcase"):
            parts = (case.get("classname") or "").split(".")
            for end in range(len(parts), 0, -1):  # The class name follows the dotted path.
                path = posixpath.normpath(posixpath.join(run_root, "/".join(parts[:end]) + ".py"))
                if path in known:
                    seconds[path] = seconds.get(path, 0.0) + float(case.get("time") or 0)
                    if case.find("failure") is not None or case.find("error") is not None:
                        identity = f"{case.get('classname')}::{case.get('name')}"
                        failed.setdefault(path, set()).add(identity)
                    break
            else:
                raise ValueError(
                    "JUnit testcase cannot be matched to this checkout: "
                    + (case.get("classname") or "(no classname)")
                )
    return seconds, failed


def _executed(
    database: Path, known: dict[str, str], prefix: str, scope: tuple[str, ...], run_root: str
) -> tuple[dict[str, set[str]], dict[str, set[int]], set[str]]:
    """Test files by the source file they executed, for every source the suite loaded the
    lines that ran outside any test, and every source coverage was measuring."""

    def test_of(context: str) -> str:
        name = context.partition("|")[0].partition("::")[0]
        return posixpath.normpath(posixpath.join(run_root, name)) if name else ""

    files, contexts, pairs, lines = _executions(database, lambda name: test_of(name) not in known)
    executed: dict[str, set[str]] = {}
    loaded: dict[str, set[int]] = {}
    for file_id, context_id in pairs:
        source = files[file_id].removeprefix(prefix)
        if source in known and source.startswith(scope):
            loaded[source] = lines.get(file_id, set())
            test = test_of(contexts[context_id])
            if test in known and test != source:
                executed.setdefault(source, set()).add(test)
    measured = {name.removeprefix(prefix) for name in files.values()}
    return executed, loaded, {path for path in measured if path in known and path.startswith(scope)}


def _at_import(root: Path, loaded: dict[str, set[int]]) -> list[str]:
    """Sources with a function that ran outside any test, or first loaded inside one.

    What such a function sets up is seen by tests that never run it, so any change to these
    files counts as a change to what runs at import.
    """
    found = []
    for path, lines in loaded.items():
        if path.endswith((".py", ".pyi")):
            try:
                if not lines or runs_bodies((root / path).read_bytes(), lines):
                    found.append(path)
            except OSError:
                found.append(path)
    return sorted(found)


def _report(part: Path) -> dict[str, Any]:
    try:
        found = json.loads(part.read_text())
    except (OSError, ValueError):
        found = None
    if (
        not isinstance(found, dict)
        or found.get("format") not in {READS, "anaxigraph-reads-v1", "anaxigraph-reads-v2"}
        or "loaded" not in found
    ):
        raise ValueError(f"Not a reads report: {part}")
    return found


def _reports(report: Path) -> list[dict[str, Any]]:
    controller = _report(report)
    if not controller.get("recording"):
        parts = sorted(report.parent.glob(report.name + ".*"))
        return [controller] + [_report(part) for part in parts if part.suffix != ".children"]
    names = controller.get("workers", [])
    if len(set(names)) != len(names) or any(
        not name.startswith("gw") or not name[2:].isdigit() for name in names
    ):
        raise ValueError("Invalid recording worker inventory")
    found = [controller, *(_report(report.with_name(report.name + "." + name)) for name in names)]
    for name, worker in zip([None, *names], found):
        if (
            worker.get("worker") != name
            or not worker.get("complete")
            or any(
                worker.get(key) != controller.get(key)
                for key in ("format", "run_id", "session_id", "environment")
            )
        ):
            raise ValueError("Missing, incomplete or mixed recording workers")
    return _children(report, found)


def _children(report: Path, parents: list[dict]) -> list[dict]:
    """An absent or untrusted child receipt keeps its initiating readers opaque."""
    entries = {}
    path = report.with_name(report.name + ".children")
    try:
        if path.stat().st_size <= 10_000_000:
            for line in path.read_text().splitlines(keepends=True):
                if line.endswith("\n"):
                    row = json.loads(line)
                    entries[row["id"]] = row
    except (OSError, ValueError, KeyError, TypeError):
        entries = {}  # Partial output must never remove a fallback.
    found = list(parents)
    for parent in parents:
        for identity, readers in parent.get("children", {}).items():
            row = entries.get(identity, {})
            if _completed_child(row, parent):
                observed = {
                    kind: dict.fromkeys(paths, readers)
                    for kind, paths in row["observations"].items()
                }
                observed["opened"].update(observed["loaded"])
                found.append(observed)
            else:
                parent["started"].setdefault("", []).extend(readers)
    return found


def _completed_child(row: dict, parent: dict) -> bool:
    observed = row.get("observations")
    return bool(
        isinstance(observed, dict)
        and {"opened", "listed", "started", "loaded"} <= observed.keys()
        and all(
            isinstance(paths, list) and all(isinstance(path, str) for path in paths)
            for paths in observed.values()
        )
        and all(row.get(key) == parent.get(key) for key in ("run_id", "session_id", "root"))
        and row.get("format") == parent.get("format")
        and type(row.get("finished_at")) in (int, float)
        and 0 < row.get("finished_at", 0) <= parent.get("observed_until", 0)
    )


def _reads(report: Path, known: dict[str, str]) -> dict[str, dict[str, set[str]]]:
    """What was read besides code, per file or folder, from the recorder and its workers."""
    folders = {""}
    for path in known:
        folder = posixpath.dirname(path)
        while folder not in folders:
            folders.add(folder)
            folder = posixpath.dirname(folder)
    kept = {"opened": known, "listed": folders, "started": {""}, "loaded": known}
    merged: dict[str, dict[str, set[str]]] = {kind: {} for kind in kept}
    for found in _reports(report):
        for kind, paths in merged.items():
            for path in found[kind].keys() & kept[kind]:  # Only what the checkout holds.
                paths.setdefault(path, set()).update(found[kind][path])
    named = {by for kind in merged.values() for found in kind.values() for by in found}
    if any(not by.startswith("@") for by in named) and named.isdisjoint(known):
        raise ValueError(
            "The reads report names no test file of this checkout; "
            "set ANAXIGRAPH_ROOT to the checkout when recording"
        )
    return merged


def _marked(
    found: dict[str, set[str]], mark: str, known: dict[str, str], runner: bool
) -> dict[str, list[str]]:
    """Per path, the modules among its readers that carry a mark. "" stands for a reader the
    checkout does not hold, kept only where the test run itself counts as one: not for
    Python source, which a runner reads to show a line of it and loads to depend on it."""
    result = {}
    for path, readers in found.items():
        names = {reader[1:] for reader in readers if reader.startswith(mark)}
        names = {name if name in known else "" for name in names}
        if not runner or path.endswith(".py"):
            names.discard("")
        if names:
            result[path] = sorted(names)
    return result


def _attributed(
    reads: dict[str, dict[str, set[str]]], position: dict[str, int], known: dict[str, str]
) -> dict[str, Any]:
    """Readers as test numbers, and as modules: `by_modules` for what was read while a module
    was imported, `through` for the code that did the reading inside a test.

    A file read by the run itself, with none of the checkout's code on the stack, keeps ""
    as its module; a folder listed that way is how the runner finds tests, and is left out.
    """
    result: dict[str, Any] = {"by_modules": {}, "through": {}}
    for kind in ("opened", "listed"):
        result[kind] = {
            path: sorted(position[reader] for reader in readers & position.keys())
            for path, readers in reads[kind].items()
            if readers & position.keys()
        }
        result["by_modules"][kind] = _marked(reads[kind], "@", known, kind == "opened")
        result["through"][kind] = _marked(reads[kind], "+", known, False)
    started = reads["started"].get("", set())
    result["started"] = sorted(position[reader] for reader in started & position.keys())
    modules = {reader[1:] for reader in started if reader.startswith("+") and reader[1:] in known}
    result["process_modules"] = sorted(modules)
    result["unattributed_processes"] = sorted(
        started - position.keys() - {"+" + module for module in modules}
    )
    return result


def _unloaded(
    root: Path,
    scope: tuple[str, ...],
    known: dict[str, str],
    ran: set[str],
    measured: set[str],
    read: dict[str, dict[str, set[str]]] | None,
) -> list[str]:
    """Python files of the scope that the run is known not to have loaded.

    With reads, those neither coverage nor the recorder saw. Without, those coverage lists
    without a line although they hold code that runs on import; a file coverage was not
    measuring, or an empty one, may have been loaded all the same.
    """

    def runs(path: str) -> bool:
        try:
            body = ast.parse((root / path).read_bytes()).body
        except (OSError, SyntaxError, ValueError):
            return False
        return any(  # Only a docstring leaves no line behind when a file is loaded.
            not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Constant)
            for node in body
        )

    if read is not None:
        idle = {path for path in known if path.startswith(scope) and path.endswith(".py")}
        return sorted(idle - ran - read["loaded"].keys())
    return sorted(path for path in measured - ran if runs(path))


def _fingerprints(graph: Graph, loaded: dict[str, set[int]]) -> tuple[dict[str, Any], list[str]]:
    """What a later checkout is compared with, per loaded source: the whole file when its
    functions ran outside any test, else only what runs at import."""
    nodes = {node.path: node for node in graph.nodes if node.kind == "module"}
    whole = set(_at_import(Path(graph.snapshot.root), loaded))
    kinds = {path: "semantic" if path in whole else "import" for path in loaded if path in nodes}
    found = {path: nodes[path].data.get(f"{kind}_fingerprint") for path, kind in kinds.items()}
    return found, sorted(whole)


def _test_files(
    seconds: dict[str, float],
    executed: dict[str, set[str]],
    read: dict[str, dict[str, set[str]]],
    known: dict[str, str],
) -> list[str]:
    """Every test file the run's reports name."""
    found = set(seconds).union(*executed.values())
    for kind in read.values():
        found.update(*(readers & known.keys() for readers in kind.values()))
    return sorted(found)


def _recorded_reads(report: Path | None, known: dict, executed: dict) -> dict | None:
    read = _reads(report, known) if report else None
    for path, readers in (read or {}).get("loaded", {}).items():
        owners = readers & known.keys()
        if owners:
            executed.setdefault(path, set()).update(owners)
    return read


def record_from_coverage(
    graph: Graph,
    database: Path,
    *,
    suite: str,
    scope: list[str],
    run_root: str = ".",
    recorded_root: str | None = None,
    junit: list[Path] | None = None,
    reads: Path | None = None,
) -> dict[str, Any]:
    """A record for one suite. `run_root` is the folder the suite ran in, relative to the
    checkout; `recorded_root` is the checkout's path on the machine that recorded the run;
    `junit` and `reads` are the JUnit reports and the reads recorder's report of that run.
    """
    known = graph.snapshot.files
    reports = junit or []
    prefix = (recorded_root or graph.snapshot.root).rstrip("/") + "/"
    folders = tuple("" if item == "." else item + "/" for item in map(posixpath.normpath, scope))
    provenance = verify(reads, graph, database, reports)
    executed, loaded, measured = _executed(database, known, prefix, folders, run_root)
    seconds, failed = junit_cases(reports, run_root, known)
    read = _recorded_reads(reads, known, executed)
    idle = _unloaded(Path(graph.snapshot.root), folders, known, set(loaded), measured, read)
    tests = _test_files(seconds, executed, read or {}, known)
    position = {test: index for index, test in enumerate(tests)}
    imports, at_import = _fingerprints(graph, loaded)
    # With reads, a file anywhere in the checkout can be a test's input.
    compared = known if read else {path: known[path] for path in known if path.startswith(folders)}
    return {
        "format": FORMAT,
        "provenance": provenance,
        "suite": suite,
        "scope": list(folders),
        "run_root": run_root,
        "revision": graph.snapshot.revision,
        "files": dict(compared),
        "imports": imports,
        "ran_at_import": at_import,
        "unloaded": idle,
        "tests": tests,
        "seconds": [round(seconds.get(test, 0.0), 3) for test in tests],
        "failed": sorted(position[test] for test in failed),  # Already failing when recorded.
        "failed_cases": {path: sorted(cases) for path, cases in failed.items()},
        "executed": {path: sorted(map(position.__getitem__, by)) for path, by in executed.items()},
        "reads": _attributed(read, position, known) if read else None,
    }
