"""Bounded workflow measurements, separate from model results and scheduling."""

from __future__ import annotations

import json
import time
from typing import Any


def plan_batch(run, kind, scope, **options):
    if "next_batch_id" not in run:
        run["next_batch_id"] = max((batch["id"] for batch in run["batches"]), default=0) + 1
    batch = {
        "id": run["next_batch_id"],
        "kind": kind,
        "scope": scope,
        "status": "pending",
        "attempts": 0,
        **options,
    }
    run["next_batch_id"] += 1
    run["batches"].append(batch)
    return batch


def attempt_summary(run: dict[str, Any]) -> dict[str, Any]:
    """Count recorded failures, including fallback candidates, without inventing old history."""
    batches = run["batches"]
    failures = [
        {"batch_id": batch["id"], **failure}
        for batch in batches
        for failure in batch.get("attempt_failures", [])
    ]
    count = sum(batch.get("failure_count", 0) for batch in batches)
    return {
        "batch_attempts": sum(batch["attempts"] for batch in batches),
        "batch_retries": sum(max(0, batch["attempts"] - 1) for batch in batches),
        "failed_attempts": count,
        "attempt_history_complete": run.get("attempt_history_version") == 1,
        "attempt_failures": sorted(failures, key=lambda failure: failure["at"], reverse=True)[:10],
        "attempt_failures_truncated": count > min(len(failures), 10),
    }


def start_batch(batch: dict[str, Any]) -> tuple[float, int]:
    started = time.monotonic()
    batch["started_at"] = time.time()
    batch.pop("finished_at", None)
    for field in ("elapsed_seconds", "model_seconds", "model_calls"):
        batch.setdefault(field, 0)
    return started, batch["model_calls"]


def finish_batch(batch: dict[str, Any], started: float, calls_before: int) -> None:
    elapsed = round(time.monotonic() - started, 4)
    batch["finished_at"] = time.time()
    batch["elapsed_seconds"] = round(batch.get("elapsed_seconds", 0) + elapsed, 4)
    batch["attempt_history"] = (
        batch.get("attempt_history", [])
        + [
            {
                "attempt": batch["attempts"],
                "started_at": batch["started_at"],
                "finished_at": batch["finished_at"],
                "elapsed_seconds": elapsed,
                "status": batch["status"],
                "reused": bool(batch.get("reused")),
                "model_calls": batch.get("model_calls", 0) - calls_before,
            }
        ]
    )[-3:]


def record_model(run, batch, request, worker, elapsed, usage, *, reused=False, review=False):
    if reused:
        return
    batch["model_calls"] = batch.get("model_calls", 0) + 1
    batch["model_seconds"] = round(batch.get("model_seconds", 0) + elapsed, 4)
    key = json.dumps([request["kind"], worker, review], sort_keys=True)
    row = run.setdefault("model_usage", {}).setdefault(
        key,
        {
            "stage": request["kind"],
            "provider": worker["provider"],
            "model": worker["model"],
            "reasoning_effort": worker.get("reasoning_effort"),
            "review": review,
            "calls": 0,
            "elapsed_seconds": 0,
            "known_output_tokens": 0,
            "unknown_output_calls": 0,
        },
    )
    row["calls"] += 1
    row["elapsed_seconds"] = round(row["elapsed_seconds"] + elapsed, 4)
    tokens = (usage or {}).get("output_tokens")
    if type(tokens) is int and tokens >= 0:
        row["known_output_tokens"] += tokens
    else:
        row["unknown_output_calls"] += 1


def _stage_timings(batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stages = []
    for kind in dict.fromkeys(batch["kind"] for batch in batches):
        selected = [batch for batch in batches if batch["kind"] == kind]
        stages.append(
            {
                "stage": kind,
                "total_batches": len(selected),
                "completed_batches": sum(batch["status"] == "completed" for batch in selected),
                "elapsed_seconds": round(
                    sum(batch.get("elapsed_seconds", 0) for batch in selected), 4
                ),
                "model_calls": sum(batch.get("model_calls", 0) for batch in selected),
                "model_seconds": round(sum(batch.get("model_seconds", 0) for batch in selected), 4),
                "reused_batches": sum(bool(batch.get("reused")) for batch in selected),
            }
        )
    return stages


def timing_summary(
    run: dict[str, Any],
    batch_offset: int = 0,
    batch_limit: int = 10,
    *,
    include_details: bool = True,
) -> dict[str, Any]:
    batches = run["batches"]
    summary: dict[str, Any] = {
        "timing_complete": run.get("timing_version") == 1,
        "stage_timings": _stage_timings(batches),
        "batch_timings_total": len(batches),
    }
    if not include_details:
        return summary
    models = [dict(row) for row in run.get("model_usage", {}).values()]
    for row in models:
        row["output_tokens"] = None if row["unknown_output_calls"] else row["known_output_tokens"]
    fields = (
        "id",
        "kind",
        "scope",
        "lens",
        "status",
        "attempts",
        "started_at",
        "finished_at",
        "elapsed_seconds",
        "model_seconds",
        "model_calls",
        "reused",
        "skipped",
        "attempt_history",
    )
    next_offset = batch_offset + batch_limit
    return summary | {
        "model_usage": models,
        "batch_timings": [
            {key: batch.get(key) for key in fields} for batch in batches[batch_offset:next_offset]
        ],
        "next_batch_offset": next_offset if next_offset < len(batches) else None,
    }
