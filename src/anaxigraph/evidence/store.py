"""A small local trial ledger shared by treatment and control experiment arms.

Events distinguish context supplied from entities actually inspected. This records
agent/provider reports, not independently attested correctness or a paid invoice.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from anaxigraph.evidence.usage import FIELDS

ALLOWED = set(
    (
        "task_id task starting_revision starting_snapshot_id baseline_id continued snapshot_id revision "
        "entity_id entity_ids operation files findings_count status notes receipts provider model stage "
        "elapsed_seconds request_id reused attempt input_tokens output_tokens cached_input_tokens "
        "cache_write_input_tokens uncached_input_tokens reasoning_output_tokens total_tokens cost_usd "
        "usage_source cost_source reasoning_effort reasoning_effort_basis run_id experiment_id arm metrics starting_commit candidate_commit "
        "check passed failed unchecked stale reported_passed outcome phase error_kind checks duration_seconds batch_id"
    ).split()
)
FORBIDDEN = set(
    (
        "api_key password secret authorization prompt response source raw_source stdout stderr body "
        "content access_token refresh_token"
    ).split()
)


def _encode(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _bounded(value: Any, depth: int = 0) -> None:
    if depth > 5:
        raise ValueError("Evidence nesting exceeds its budget")
    if isinstance(value, str) and len(value) > 4000:
        raise ValueError("Evidence text must be at most 4,000 characters")
    if isinstance(value, (list, dict)):
        if len(value) > 100:
            raise ValueError("Evidence collections must contain at most 100 members")
        if isinstance(value, dict):
            if any(not isinstance(key, str) or key.lower() in FORBIDDEN for key in value):
                raise ValueError("Evidence must not contain source, transcripts or credentials")
            values: Iterable[Any] = value.values()
        else:
            values = value
        for item in values:
            _bounded(item, depth + 1)


class Evidence:
    """Constructing or disabling a ledger never writes to the repository."""

    def __init__(
        self,
        root: Path,
        *,
        enabled: bool = True,
        arm: str = "unassigned",
        experiment_id: str | None = None,
        max_bytes: int = 5_000_000,
        max_events: int = 5000,
    ) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir() or not isinstance(enabled, bool):
            raise ValueError("Evidence needs a repository directory and boolean enabled setting")
        if not isinstance(arm, str) or not 1 <= len(arm) <= 100:
            raise ValueError("Trial arm must be text of at most 100 characters")
        if experiment_id is not None and (
            not isinstance(experiment_id, str) or len(experiment_id) > 200
        ):
            raise ValueError("Experiment identity must be text of at most 200 characters")
        if (
            type(max_bytes) is not int
            or max_bytes < 65_536
            or type(max_events) is not int
            or max_events < 1
        ):
            raise ValueError("Evidence budgets must allow at least 64 KiB and one event")
        self.enabled, self.arm, self.experiment_id = enabled, arm, experiment_id
        self.max_bytes, self.max_events = max_bytes, max_events
        self.path = self.root / ".anaxigraph-v2" / "trial.sqlite"

    def _safe(self) -> None:
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise ValueError("Evidence storage must not follow symlinks")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._safe()
        self.path.parent.mkdir(exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=DELETE")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            db.execute(f"PRAGMA max_page_count={self.max_bytes // page_size}")
            db.execute(
                "CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at TEXT NOT NULL, task_id TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            yield db
            db.commit()
        except sqlite3.Error as error:
            db.rollback()
            raise ValueError(
                "Trial evidence could not be saved within its storage budget"
            ) from error
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _get(db: sqlite3.Connection, key: str, default: Any = None) -> Any:
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def _set(db: sqlite3.Connection, key: str, value: Any) -> None:
        db.execute(
            "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, _encode(value)),
        )

    def record(
        self, kind: str, payload: dict[str, Any], *, task_id: str | None = None
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        if not isinstance(kind, str) or not kind or len(kind) > 80:
            raise ValueError("Evidence kind must be nonempty text of at most 80 characters")
        if not isinstance(payload, dict) or set(payload) - ALLOWED:
            raise ValueError("Evidence payload contains unsupported fields")
        _bounded(payload)
        body = _encode(payload)
        if len(body.encode()) > 16_000:
            raise ValueError("Evidence event exceeds its 16 KB budget")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._append(db, kind, payload, task_id)

    def _append(
        self, db: sqlite3.Connection, kind: str, payload: dict, task_id: str | None = None
    ) -> dict:
        active = task_id or payload.get("task_id") or self._get(db, "active_task")
        stamp = datetime.now(timezone.utc).isoformat()
        envelope = payload | {"arm": self.arm, "experiment_id": self.experiment_id}
        cursor = db.execute(
            "INSERT INTO events(at,task_id,kind,payload) VALUES (?,?,?,?)",
            (stamp, active, kind, _encode(envelope)),
        )
        if kind == "task_started":
            self._set(db, "active_task", payload["task_id"])
            self._set(
                db,
                "active_task_key",
                [payload["task"], payload["starting_revision"], payload["baseline_id"]],
            )
        count = db.execute("SELECT count(*) FROM events").fetchone()[0]
        if count > self.max_events:
            removed = count - self.max_events
            db.execute(
                "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id LIMIT ?)",
                (removed,),
            )
            self._set(db, "dropped_events", self._get(db, "dropped_events", 0) + removed)
        return {
            "id": cursor.lastrowid,
            "at": stamp,
            "task_id": active,
            "kind": kind,
            "payload": envelope,
        }

    def start_task(
        self,
        task: str,
        revision: str | None,
        snapshot_id: str,
        baseline_id: str,
        *,
        continue_task: bool = False,
    ) -> str | None:
        if not self.enabled:
            return None
        if not isinstance(task, str) or not task.strip() or len(task) > 4000:
            raise ValueError("Trial task must be nonempty text")
        key = [" ".join(task.split()), revision, baseline_id]
        payload = {
            "task": key[0],
            "starting_revision": revision,
            "starting_snapshot_id": snapshot_id,
            "baseline_id": baseline_id,
            "continued": continue_task,
        }
        _bounded(payload)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = self._get(db, "active_task")
            if existing and (continue_task or self._get(db, "active_task_key") == key):
                return existing
            identity = uuid.uuid4().hex
            self._append(db, "task_started", payload | {"task_id": identity}, identity)
            return identity

    def consulted(
        self, entity_id: str, snapshot_id: str, *, operation: str = "inspect"
    ) -> dict | None:
        return self.record(
            "context_consulted",
            {"entity_id": entity_id, "snapshot_id": snapshot_id, "operation": operation},
        )

    def outcome(
        self,
        status: str,
        snapshot_id: str | None = None,
        receipts: list[dict[str, Any]] | None = None,
        notes: str | None = None,
    ) -> dict | None:
        return self.record(
            "task_outcome",
            {"status": status, "snapshot_id": snapshot_id, "receipts": receipts, "notes": notes},
        )

    def model_call(
        self,
        provider: str,
        model: str,
        stage: str,
        status: str,
        elapsed_seconds: float,
        *,
        usage: dict[str, Any] | None = None,
        request_id: str | None = None,
        reused: bool = False,
        attempt: int = 1,
        reasoning_effort: str | None = None,
    ) -> dict | None:
        usage = usage or {}
        if (
            isinstance(elapsed_seconds, bool)
            or not isinstance(elapsed_seconds, (float, int))
            or not math.isfinite(elapsed_seconds)
            or elapsed_seconds < 0
        ):
            raise ValueError("Model elapsed time must be a finite nonnegative number")
        allowed_usage = {key: usage.get(key) for key in FIELDS}
        allowed_usage.update(
            usage_source=usage.get("usage_source", "unknown"),
            cost_source=usage.get("cost_source", "unknown"),
        )
        return self.record(
            "model_call",
            {
                "provider": provider,
                "model": model,
                "stage": stage,
                "phase": "provider",
                "status": status,
                "elapsed_seconds": elapsed_seconds,
                "request_id": request_id,
                "reused": reused,
                "attempt": attempt,
                "reasoning_effort": reasoning_effort,
                "reasoning_effort_basis": "explicit-cli-config"
                if reasoning_effort is not None
                else "provider-default-unverified",
                **allowed_usage,
            },
        )

    def events(
        self, limit: int = 50, offset: int = 0, *, task_id: str | None = None
    ) -> dict[str, Any]:
        if (
            type(limit) is not int
            or not 1 <= limit <= 1000
            or type(offset) is not int
            or offset < 0
        ):
            raise ValueError("Evidence pages need a limit of 1–1,000 and nonnegative offset")
        self._safe()
        if not self.enabled or not self.path.exists():
            return {"items": [], "total": 0, "next_offset": None}
        with self._connection() as db:
            clause, arguments = (" WHERE task_id=?", [task_id]) if task_id else ("", [])
            total = db.execute("SELECT count(*) FROM events" + clause, arguments).fetchone()[0]
            rows = db.execute(
                "SELECT * FROM events" + clause + " ORDER BY id LIMIT ? OFFSET ?",
                [*arguments, limit, offset],
            )
            items = [dict(row) | {"payload": json.loads(row["payload"])} for row in rows]
        return {
            "items": items,
            "total": total,
            "next_offset": offset + len(items) if offset + len(items) < total else None,
        }

    def summary(self) -> dict[str, Any]:
        self._safe()
        if not self.enabled or not self.path.exists():
            return {
                "enabled": self.enabled,
                "events": 0,
                "bytes": 0,
                "dropped_events": 0,
                "known_cost_usd": None,
                "unknown_cost_calls": 0,
                "cost_complete": None,
                "complete": True,
            }
        with self._connection() as db:
            db.execute("BEGIN")
            return self._summary(db)

    def latest(self, kind: str) -> dict[str, Any] | None:
        """Read the last retained event for the active task, without scanning usage."""
        self._safe()
        if not self.enabled or not self.path.exists():
            return None
        with self._connection() as db:
            active = self._get(db, "active_task")
            row = db.execute(
                "SELECT payload FROM events WHERE task_id=? AND kind=? ORDER BY id DESC LIMIT 1",
                (active, kind),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def _summary(self, db: sqlite3.Connection) -> dict[str, Any]:
        kinds = {
            row[0]: row[1] for row in db.execute("SELECT kind,count(*) FROM events GROUP BY kind")
        }
        calls = [
            json.loads(row[0])
            for row in db.execute("SELECT payload FROM events WHERE kind='model_call'")
        ]
        dropped = self._get(db, "dropped_events", 0)
        active = self._get(db, "active_task")
        known = [
            item["cost_usd"]
            for item in calls
            if isinstance(item.get("cost_usd"), (int, float))
            and not isinstance(item["cost_usd"], bool)
        ]
        return {
            "enabled": True,
            "events": sum(kinds.values()),
            "event_kinds": kinds,
            "bytes": self.path.stat().st_size,
            "max_bytes": self.max_bytes,
            "arm": self.arm,
            "experiment_id": self.experiment_id,
            "active_task_id": active,
            "dropped_events": dropped,
            "complete": dropped == 0,
            "known_cost_usd": sum(known) if known else None,
            "unknown_cost_calls": len(calls) - len(known),
            "cost_complete": bool(calls) and len(known) == len(calls) and dropped == 0,
            "cost_basis": "provider-reported estimates; missing values remain unknown",
        }

    def export(self, path: Path | None = None) -> dict[str, Any]:
        self._safe()
        summary, items = self.summary(), []
        if self.enabled and self.path.exists():
            with self._connection() as db:
                db.execute("BEGIN")
                summary = self._summary(db)
                items = [
                    dict(row) | {"payload": json.loads(row["payload"])}
                    for row in db.execute("SELECT * FROM events ORDER BY id")
                ]
        record = {"format": "anaxigraph-trial-v1", "summary": summary, "events": items}
        if path is not None:
            path = Path(path)
            if path.is_symlink() or path.parent.is_symlink():
                raise ValueError("Evidence export must not follow symlinks")
            raw = (_encode(record) + "\n").encode()
            if len(raw) > self.max_bytes:
                raise ValueError("Evidence export exceeds its size budget")
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=path.parent, prefix=".trial-", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return record
