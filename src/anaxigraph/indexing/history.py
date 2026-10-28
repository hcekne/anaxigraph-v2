"""Capture bounded local Git change sets, without treating them as dependencies."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .discovery import _git


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
