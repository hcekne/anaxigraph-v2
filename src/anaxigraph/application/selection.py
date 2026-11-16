"""Test records of this checkout, and the tests it needs according to them."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from anaxigraph.analysis.test_selection import select_tests
from anaxigraph.indexing import worktrees
from anaxigraph.indexing.test_record import FORMAT, junit_cases, record_from_coverage

_FOLDER = Path(".anaxigraph-v2") / "test-records"
_SHOWN = 200


def record_tests(
    app: Any,
    coverage: Path,
    suite: str,
    scope: list[str],
    run_root: str = ".",
    recorded_root: str | None = None,
    junit: list[Path] | None = None,
    reads: Path | None = None,
) -> dict[str, Any]:
    """Save a record of one suite's run, taken at the checkout as it is now."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", suite):
        raise ValueError("A suite name is lowercase letters, digits and hyphens")
    if not scope or any(not isinstance(item, str) or not item.strip("/") for item in scope):
        raise ValueError("Give at least one folder the suite depends on, such as backend/")
    record = record_from_coverage(
        app._live(),
        coverage,
        suite=suite,
        scope=scope,
        run_root=run_root,
        recorded_root=recorded_root,
        junit=junit,
        reads=reads,
    )
    if not record["tests"]:
        raise ValueError("The run names no test file of this checkout; check --run-root")
    target = app.root / _FOLDER / f"{suite}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, separators=(",", ":")))
    temporary.replace(target)
    return {
        "suite": suite,
        "path": str(target),
        "test_files": len(record["tests"]),
        "sources_executed": len(record["executed"]),
        "files_compared": len(record["files"]),
        "ran_at_import": len(record["ran_at_import"]),
        "reads_recorded": record["reads"] is not None,
        "bytes": target.stat().st_size,
    }


def _records(app: Any) -> list[dict[str, Any]]:
    """This checkout's records, or those of the worktree that recorded most recently."""
    folders = [app.root / _FOLDER] + [path / _FOLDER for path in worktrees(app.root)]
    found = [sorted(folder.glob("*.json")) for folder in dict.fromkeys(folders) if folder.is_dir()]
    found = [files for files in found if files]
    if not found[:1]:
        return []
    if not (app.root / _FOLDER).is_dir() or not sorted((app.root / _FOLDER).glob("*.json")):
        found.sort(key=lambda files: -max(file.stat().st_mtime for file in files))
    records = [json.loads(file.read_text()) for file in found[0]]
    return [record for record in records if record.get("format") == FORMAT]


def _shadow(record: dict[str, Any], picked: dict[str, Any], graph: Any, ran: list[Path]) -> dict:
    """Would the selection have caught what a whole-suite run of this checkout caught?"""
    failing = junit_cases(ran, record["run_root"], graph.snapshot.files)[1]
    before = record.get("failed_cases", {})
    new = {path for path, cases in failing.items() if cases - set(before.get(path, []))}
    left_out = new - set(picked["test_files"])
    return {
        "failing_test_files": len(failing),
        "failing_when_recorded": sum(
            bool(cases & set(before.get(path, []))) for path, cases in failing.items()
        ),
        "caught": len(new) - len(left_out) if picked["status"] != "all" else None,
        "missed": sorted(left_out) if picked["status"] != "all" else [],
    }


def tests_for_change(app: Any, full: bool = False, ran: list[Path] | None = None) -> dict[str, Any]:
    """The test files each recorded suite needs for the checkout as it is now.

    `ran` names JUnit reports of a whole-suite run at this checkout; each suite then says
    which failing test files its selection would have left out.
    """
    graph = app._live()
    suites = []
    for record in _records(app):
        picked = select_tests(record, graph)
        if ran:
            picked["shadow"] = _shadow(record, picked, graph, ran)
        if not full and len(picked["test_files"]) > _SHOWN:
            picked |= {"test_files": picked["test_files"][:_SHOWN], "test_files_truncated": True}
        suites.append(picked)
    if not suites:
        return {
            "status": "no_record",
            "suites": [],
            "next_step": "Run the suite once with one coverage context per test and the reads "
            "recorder, then save it with `anaxigraph record-tests`. Until then every test is "
            "needed.",
        }
    return {"status": "recorded", "snapshot_id": graph.snapshot.id, "suites": suites}
