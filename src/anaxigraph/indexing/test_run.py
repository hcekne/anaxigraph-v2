"""Verify the source state and completed outputs of one recorded pytest invocation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from anaxigraph.contracts import Graph

FORMAT = "anaxigraph-test-run-v1"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(report: Path | None, graph: Graph, coverage: Path | None, junit: list[Path]) -> dict:
    if report is None:
        return {"status": "unverified", "reason": "No source-bound reads recording"}
    found = json.loads(report.read_text())
    if not isinstance(found, dict):
        raise ValueError("Not a reads report")
    run = found.get("recording")
    if run is None:
        return {"status": "unverified", "reason": "Legacy recording has no source/run binding"}
    if not isinstance(run, dict) or run.get("format") != FORMAT or not run.get("run_id"):
        raise ValueError("Invalid recording manifest")
    if run.get("files") != graph.snapshot.files:
        raise ValueError(
            "Recording source differs from this checkout; preserve the original record"
        )
    if not found.get("complete") or found.get("run_id") != run["run_id"]:
        raise ValueError("Recording did not complete against its original source")
    artifacts = found.get("artifacts", {})
    if coverage is not None and artifacts.get("coverage") != digest(coverage):
        raise ValueError("Coverage does not belong to this completed recording")
    if len(junit) != 1 or artifacts.get("junit") != digest(junit[0]):
        raise ValueError("JUnit does not belong to this completed recording")
    return {
        "status": "verified",
        "run_id": run["run_id"],
        "environment": found["environment"],
        "observations": found.get("format"),
    }
