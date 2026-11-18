"""Files that changed together in past commits: an association to look at, not a dependency.

Measured as a ranking signal in `replay_navigation.py`; it did not beat keyword search clearly
enough to stay in the product, so it lives here for the experiments that still use it.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path

from anaxigraph.indexing.discovery import _git


def capture_history(root: Path, revision: str | None, eligible: set[str]) -> dict:
    if revision is None:
        return {"status": "unavailable", "reason": "No committed Git history", "changes": []}
    raw = _git(
        root,
        "log",
        "--no-merges",
        "--no-renames",
        "-1000",
        "--format=%x00%H%x00",
        "--name-only",
        "-z",
        "--relative",
        revision,
        "--",
        ".",
    )
    if raw is None or len(raw) > 4_000_000:
        return {
            "status": "unavailable",
            "reason": "History unavailable within capture bounds",
            "changes": [],
        }
    blocks = re.split(rb"\x00[0-9a-f]{40,64}\x00\x00\n", raw)[1:]
    changes = []
    for block in blocks:
        paths = {os.fsdecode(value) for value in block.split(b"\0") if value}
        changes.append({"size": len(paths), "paths": sorted(paths & eligible)})
    return {
        "status": "available",
        "through_revision": revision,
        "commits_examined": len(changes),
        "history_limit": 1000,
        "history_may_be_truncated": len(changes) == 1000,
        "changes": changes,
    }


def cochange_advice(history: dict, targets: set[str], limit: int = 6) -> dict:
    counts: Counter[str] = Counter()
    witnesses: dict[str, set[str]] = {}
    support = 0
    for change in history.get("changes", []):
        paths = set(change["paths"])
        sources = paths & targets
        if not sources or change["size"] > 30:
            continue
        support += 1
        for path in paths - targets:
            counts[path] += 1
            witnesses.setdefault(path, set()).update(sources)
    return {
        **{key: value for key, value in history.items() if key != "changes"},
        "basis": "historical-co-change",
        "supporting_commits": support,
        "suggestions": [
            {
                "path": path,
                "cochange_commits": counts[path],
                "with_paths": sorted(witnesses[path])[:3],
                "support_fraction": round(counts[path] / support, 3),
            }
            for path in sorted(counts, key=lambda path: (-counts[path], path))[:limit]
        ],
        "suggestions_total": len(counts),
        "interpretation": "Inspect these associations; they do not require edits or tests, prove dependencies, or authorize skipping checks.",
    }
