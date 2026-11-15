"""Measure what the structural map gives on a real checkout, and how fast it answers.

Everything runs on a throwaway clone, so the checkout itself is never written to, and no model
is called. Run it before and after a change to indexing, storage or guidance:

    uv run python scripts/map_health.py /path/to/checkout --json before.json
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

FRESH = (
    "import json,sys,time;t=time.monotonic();"
    "from pathlib import Path;from anaxigraph.application.core import Application;"
    "r=Application(Path(sys.argv[1])).scan();"
    "print(json.dumps({'seconds':round(time.monotonic()-t,2),'indexing':r.get('indexing')}))"
)


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True, text=True, check=True
    ).stdout


def _timed(function, *arguments, **options) -> tuple[float, Any]:
    started = time.monotonic()
    result = function(*arguments, **options)
    return round(time.monotonic() - started, 2), result


def _fresh(checkout: Path) -> dict[str, Any]:
    """A new process: what a newly started MCP server pays before its first answer."""
    output = subprocess.run(
        [sys.executable, "-c", FRESH, str(checkout)],
        capture_output=True,
        text=True,
        check=True,
        env=os.environ | {"ANAXIGRAPH_EVIDENCE": "off"},
    ).stdout
    return json.loads(output.strip().splitlines()[-1])


def _probe_file(application) -> str:
    """A mid-sized production Python module, chosen deterministically."""
    modules = sorted(
        (item["data"].get("loc", 0), item["path"])
        for item in _all_modules(application)
        if item["path"].endswith(".py") and not item["data"].get("is_test")
    )
    if not modules:
        raise SystemExit("No production Python module to edit in this checkout")
    return modules[len(modules) // 2][1]


def _all_modules(application) -> list[dict[str, Any]]:
    items, offset = [], 0
    while offset is not None:
        page = application.modules(limit=100, offset=offset)
        items.extend(page["items"])
        offset = page["next_offset"]
    return items


def _refusals(application, target: Path, original: str, task: str) -> int:
    """Calls refused only because the checkout changed since the last scan."""
    refused = 0
    target.write_text(original + "\n# map-health probe\n")
    for call in (lambda: application.prepare_change(task), application.validate_change):
        try:
            call()
        except ValueError as error:
            refused += "Checkout changed" in str(error)
    target.write_text(original)
    return refused


def measure(checkout: Path, tasks: int) -> dict[str, Any]:
    from anaxigraph.application.core import Application

    with tempfile.TemporaryDirectory(prefix="anaxigraph-map-health-") as scratch:
        clone, worktree = Path(scratch) / "clone", Path(scratch) / "worktree"
        subprocess.run(
            ["git", "clone", "--quiet", "--shared", str(checkout), str(clone)], check=True
        )
        application = Application(clone)
        timings: dict[str, Any] = {}
        timings["first_scan"], status = _timed(application.scan)
        timings["rescan_unchanged"], _ = _timed(application.scan)
        target = clone / _probe_file(application)
        original = target.read_text()
        target.write_text(original + "\n# map-health probe\n")
        timings["rescan_comment_edit"], _ = _timed(application.scan)
        target.write_text(original + "\n\ndef _map_health_probe():\n    return 1\n")
        timings["rescan_added_function"], _ = _timed(application.scan)
        target.write_text(original)
        # A new server process that meets an edit nobody has scanned yet.
        timings["new_process_after_edit"] = _fresh(clone)["seconds"]
        application.scan()
        note = clone / "zz-map-health-probe.md"
        note.write_text("probe\n")
        timings["rescan_added_markdown"], _ = _timed(application.scan)
        note.unlink()
        application.scan()
        timings["new_process_unchanged"] = _fresh(clone)["seconds"]
        # A second checkout of the same repository starts from what the first one saved.
        _git(clone, "worktree", "add", "--quiet", "--detach", str(worktree))
        timings["new_worktree"] = _fresh(worktree)["seconds"]
        subjects = [
            line
            for line in _git(
                clone, "log", "-n", str(tasks * 3), "--no-merges", "--format=%s"
            ).splitlines()
            if len(line) > 15
        ][:tasks]
        briefs, sizes = [], []
        for subject in subjects:
            seconds, brief = _timed(application.prepare_change, subject)
            briefs.append(seconds)
            sizes.append(len(json.dumps(brief)))
        refused = _refusals(application, target, original, subjects[0]) if subjects else 0
        application.scan()
        status = application.status()
        return {
            "anaxigraph": status["version"],
            "modules": status["modules"],
            "source_loc": status["source_loc"],
            "symbols": status["symbols"],
            "relationships": status["relationships"],
            "unresolved_relationships": status["unresolved_relationships"],
            "map_health": status.get("map_health"),
            "seconds": timings,
            "brief_seconds_median": statistics.median(briefs) if briefs else None,
            "brief_kilobytes_median": round(statistics.median(sizes) / 1000, 1) if sizes else None,
            "calls_refused_for_changed_checkout": refused,
            "peak_memory_megabytes": round(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            ),
            "diagnostics": status["diagnostics"][:6],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--tasks", type=int, default=5, help="Recent commit subjects used as tasks")
    parser.add_argument("--json", type=Path, help="Also write the report to this file")
    arguments = parser.parse_args()
    os.environ.setdefault("ANAXIGRAPH_EVIDENCE", "off")
    report = measure(arguments.checkout.resolve(strict=True), arguments.tasks)
    text = json.dumps(report, indent=2)
    if arguments.json:
        arguments.json.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
