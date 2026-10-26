"""Bounded experiment evidence and descriptive comparison, independent of AnaxiGraph."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARMS = {"treatment", "control"}
KINDS = {
    "task_started",
    "context_consulted",
    "coding_call",
    "analysis_call",
    "task_outcome",
    "acceptance",
    "review",
}
FIELDS = {
    "task",
    "starting_revision",
    "candidate_revision",
    "baseline_id",
    "snapshot_id",
    "model",
    "reasoning_effort",
    "session_id",
    "elapsed_seconds",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cost_usd",
    "cost_basis",
    "usage_basis",
    "status",
    "passed",
    "total",
    "notes",
    "context_ids",
    "result_path",
    "source_lines",
    "dependency_cycles",
    "review_minutes",
    "commit",
    "provider",
    "anaxigraph_commit",
}
MAX_BYTES, MAX_EVENTS = 5_000_000, 5_000


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA max_page_count={MAX_BYTES // db.execute('PRAGMA page_size').fetchone()[0]}")
    db.execute(
        "CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,arm TEXT NOT NULL,task_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL)"
    )
    return db


def record(path: Path, arm: str, task_id: str, kind: str, payload: dict[str, Any]) -> str:
    if arm not in ARMS or kind not in KINDS or not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("Choose a declared arm, task identity and event kind")
    if not isinstance(payload, dict) or set(payload) - FIELDS:
        raise ValueError(
            "Event contains unknown fields; raw source/transcripts are not trial metrics"
        )
    for key in (
        "elapsed_seconds",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "cost_usd",
        "review_minutes",
    ):
        value = payload.get(key)
        if value is not None and (
            type(value) not in (float, int) or not math.isfinite(value) or value < 0
        ):
            raise ValueError(f"{key} must be nonnegative or null when unknown")
    if payload.get("cost_usd") is not None and not payload.get("cost_basis"):
        raise ValueError("A reported cost needs its billing/estimate basis")
    if any(
        payload.get(key) is not None
        for key in ("input_tokens", "output_tokens", "cached_input_tokens")
    ) and not payload.get("usage_basis"):
        raise ValueError("Reported tokens need an observed usage basis")
    body = json.dumps(payload, sort_keys=True, allow_nan=False)
    if len(body.encode()) > 16_000:
        raise ValueError("Trial event exceeds its 16 KB budget")
    identity = uuid.uuid4().hex
    db = connect(path)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM events").fetchone()[0] >= MAX_EVENTS:
                raise ValueError(
                    "Trial event budget exhausted; export and start a declared next run"
                )
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?)",
                (identity, datetime.now(timezone.utc).isoformat(), arm, task_id, kind, body),
            )
    finally:
        db.close()
    return identity


def events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    db = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        return [
            {
                "id": row[0],
                "created_at": row[1],
                "arm": row[2],
                "task_id": row[3],
                "kind": row[4],
                "payload": json.loads(row[5]),
            }
            for row in db.execute("SELECT * FROM events ORDER BY created_at,id")
        ]
    finally:
        db.close()


def metric(rows: list[dict], field: str) -> dict[str, Any]:
    known = [row["payload"][field] for row in rows if row["payload"].get(field) is not None]
    return {
        "reported_sum": sum(known) if known else None,
        "known_events": len(known),
        "unknown_events": len(rows) - len(known),
        "complete": bool(rows) and len(known) == len(rows),
    }


def compare(path: Path, manifest: Path) -> dict[str, Any]:
    config = json.loads(manifest.read_text())
    rows = events(path)
    arms = {}
    for arm in sorted(ARMS):
        selected = [row for row in rows if row["arm"] == arm]
        coding = [row for row in selected if row["kind"] == "coding_call"]
        analysis = [row for row in selected if row["kind"] == "analysis_call"]
        outcomes = [row for row in selected if row["kind"] == "task_outcome"]
        acceptance = [row for row in selected if row["kind"] == "acceptance"]
        latest = {row["task_id"]: row["payload"] for row in acceptance}
        arms[arm] = {
            "events": len(selected),
            "task_outcomes": [row["payload"] for row in outcomes],
            "acceptance_by_task": latest,
            "coding": {
                key: metric(coding, key)
                for key in ("input_tokens", "output_tokens", "cost_usd", "elapsed_seconds")
            },
            "analysis": {
                key: metric(analysis, key)
                for key in ("input_tokens", "output_tokens", "cost_usd", "elapsed_seconds")
            },
            "context_consultations": sum(row["kind"] == "context_consulted" for row in selected),
        }
    return {
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "requested_model": config["model"],
        "requested_reasoning_effort": config["reasoning_effort"],
        "arms": arms,
        "claim": "Descriptive matched-pair evidence only; no winner inferred.",
        "limitations": [
            "A single stochastic pair does not establish causality.",
            "Unknown token/cost values are not zero.",
            "Functional checks and line counts do not establish architectural quality.",
            "AnaxiGraph analysis time/cost must be included when comparing total resources.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser("record")
    write.add_argument("--arm", choices=sorted(ARMS), required=True)
    write.add_argument("--task", required=True)
    write.add_argument("--kind", choices=sorted(KINDS), required=True)
    write.add_argument("--payload", type=Path, required=True)
    report = commands.add_parser("compare")
    report.add_argument("--manifest", type=Path, default=Path(__file__).with_name("manifest.json"))
    report.add_argument("--output", type=Path)
    commands.add_parser("export")
    args = parser.parse_args()
    if args.command == "record":
        result = {
            "event_id": record(
                args.database, args.arm, args.task, args.kind, json.loads(args.payload.read_text())
            )
        }
    elif args.command == "compare":
        result = compare(args.database, args.manifest)
    else:
        result = {"schema_version": 1, "events": events(args.database)}
    raw = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if getattr(args, "output", None):
        args.output.write_text(raw)
    print(raw, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
