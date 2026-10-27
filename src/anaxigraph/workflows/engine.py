"""Small resumable batch executor shared by CLI, MCP and HTTP.

Only progress and request identities live here. Validated annotations/reviews are
saved by Application immediately; their contents are never copied into run files.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from anaxigraph.analysis.packets import plan_scopes
from anaxigraph.workflows.progress import (
    attempt_summary,
    finish_batch,
    record_model,
    start_batch,
    timing_summary,
)
from anaxigraph.workflows.providers import configured_model, execute, reasoning_effort
from anaxigraph.workflows.scouts import (
    applied_reviewers,
    plan_scouts,
    review_choices,
    review_workflow,
)

_MUTEX = threading.RLock()
_RUNNING: dict[tuple[str, str], threading.Thread] = {}
_ACTIVE: set[tuple[str, str]] = set()
KINDS = {
    "mapping": "annotate",
    "annotate": "annotate",
    "patterns": "patterns",
    "architecture": "architecture",
    "fresh-eyes": "fresh-eyes",
}
DEFAULTS: dict[str, Any] = {
    "workers": 32,
    "timeout_seconds": 600,
    "pattern_mode": "screened",
    "stages": {
        **{
            kind: {
                "provider": "codex",
                "model": "gpt-6-luna",
                "reasoning_effort": "low",
                "fallbacks": [
                    {"provider": "codex", "model": "gpt-6.1-sol", "reasoning_effort": "medium"}
                ],
            }
            for kind in ("annotate", "pattern-screen", "architecture")
        },
        "patterns": {
            "provider": "codex",
            "model": "gpt-6.1-sol",
            "reasoning_effort": "medium",
            "fallbacks": [
                {"provider": "codex", "model": "gpt-6.1-sol", "reasoning_effort": "xhigh"}
            ],
        },
        "architecture-review": {
            "provider": "codex",
            "model": "gpt-6.1-sol",
            "reasoning_effort": "medium",
            "fallbacks": [],
        },
        "fresh-eyes": {"provider": "codex", "model": "", "fallbacks": []},
    },
    "review_passes": [],
}


def _worker(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("provider", "codex") not in {"codex", "claude"}:
        raise ValueError("Each model worker must choose provider codex or claude")
    model = value.get("model", "")
    if not isinstance(model, str) or len(model) > 150:
        raise ValueError("model must be a CLI model name, or blank for its configured default")
    selected = {"provider": value.get("provider", "codex"), "model": model}
    effort = reasoning_effort(value)
    if effort is not None:
        selected["reasoning_effort"] = effort
    return selected


def _selected_worker(value: dict[str, Any]) -> dict[str, Any]:
    selected = _worker(value)
    selected["model"] = selected["model"] or configured_model(selected["provider"])
    return selected


def _stage_choices(stages: Any) -> dict[str, Any]:
    if not isinstance(stages, dict):
        raise ValueError("stages must map analysis names to model choices")
    selected = deepcopy(DEFAULTS["stages"])
    for name, choice in stages.items():
        if name not in KINDS and name not in {"pattern-screen", "architecture-review"}:
            raise ValueError("Unknown model stage")
        fallbacks = choice.get("fallbacks", []) if isinstance(choice, dict) else []
        if not isinstance(fallbacks, list) or len(fallbacks) > 4:
            raise ValueError("Select at most four progressively stronger fallback workers")
        if name == "architecture-review" and fallbacks:
            raise ValueError(
                "Architecture finding review uses one worker; configure ordered review_passes for multiple reviewers"
            )
        selected[KINDS.get(name, name)] = _worker(choice) | {
            "fallbacks": [_worker(x) for x in fallbacks]
        }
    return selected


def _config(value: dict[str, Any] | None) -> dict[str, Any]:
    config = deepcopy(DEFAULTS)
    if value:
        if not isinstance(value, dict) or set(value) - set(DEFAULTS):
            raise ValueError(
                "Workflow settings are workers, timeout_seconds, pattern_mode, stages and review_passes"
            )
        config.update(value)
        config["stages"] = _stage_choices(value.get("stages", {}))
    if type(config["workers"]) is not int or not 1 <= config["workers"] <= 100:
        raise ValueError("workers must be between 1 and 100")
    if config["pattern_mode"] not in ("screened", "exhaustive"):
        raise ValueError("pattern_mode must be screened or exhaustive")
    timeout = config["timeout_seconds"]
    if timeout is not None and (type(timeout) is not int or not 30 <= timeout <= 3600):
        raise ValueError("timeout_seconds must be null for no deadline, or between 30 and 3600")
    passes = config["review_passes"]
    if not isinstance(passes, list) or len(passes) > 4:
        raise ValueError("Select at most four ordered review passes")
    config["review_passes"] = [_worker(x) for x in passes]
    return config


def _failure_message(error: Exception, phase: str) -> str:
    message = f"{phase} failed ({type(error).__name__})"
    if phase == "validation" and isinstance(error, ValueError):
        message = (str(error)[:2000].splitlines() or [message])[0]
    return re.sub(
        r"(?i)(?:api[_-]?key|password|token|secret)\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|\S+)",
        "[redacted]",
        message,
    )[:200]


class WorkflowRunner:
    def __init__(self, application: Any, provider: Callable = execute) -> None:
        self.application, self.provider = application, provider
        self.directory = application.root / ".anaxigraph-v2" / "workflows"
        for path in (self.directory.parent, self.directory):
            if path.is_symlink():
                raise ValueError("Workflow storage must not be a symlink")
            path.mkdir(exist_ok=True)

    def _write(self, path: Path, value: dict[str, Any]) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        if len(body) > 2_000_000:
            raise ValueError("Workflow progress exceeds its 2 MB local budget")
        progress = sum(p.stat().st_size for p in self.directory.glob("*.json") if p != path)
        if progress + len(body) > 20_000_000:
            raise ValueError("Workflow progress exceeds its aggregate 20 MB budget")
        retained = [self.application.store.path]
        evidence_path = self.directory.parent / "trial.sqlite"
        if evidence_path.exists():
            retained.append(evidence_path)
        record_directory = self.application.root / "anaxigraph"
        if record_directory.is_dir():
            retained.extend(p for p in record_directory.iterdir() if p.is_file())
        extra = sum(p.stat().st_size for p in retained if p.exists())
        if extra + progress + len(body) > 200_000_000:
            raise ValueError(
                "Retained AnaxiGraph data would exceed 200 MB; last progress is preserved"
            )
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def configuration(self, value: dict[str, Any] | None = None) -> dict[str, Any]:
        with _MUTEX:
            path = self.directory / "settings.json"
            if value is not None:
                result = _config(value)
                self._write(path, result)
                return result
            return _config(json.loads(path.read_text()) if path.exists() else None)

    def _path(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise ValueError("Unknown workflow run identity")
        return self.directory / f"{run_id}.json"

    def _load(self, run_id: str) -> dict[str, Any]:
        path = self._path(run_id)
        if not path.exists():
            raise ValueError("Workflow run not found")
        return json.loads(path.read_text())

    def _save(self, run: dict[str, Any]) -> None:
        run["updated_at"] = time.time()
        self._write(self._path(run["id"]), run)

    def _publication(self) -> dict[str, Any]:
        method = getattr(self.application, "portable_status", None)
        return method() if callable(method) else {}

    def _summary(
        self,
        run: dict[str, Any],
        publication: dict[str, Any] | None = None,
        *,
        batch_offset: int = 0,
        batch_limit: int = 10,
        include_details: bool = True,
    ) -> dict[str, Any]:
        publication = self._publication() if publication is None else publication
        batches = run["batches"]
        active = sum(batch["status"] == "running" for batch in batches)
        status = run["status"]
        if status == "running" and not self._process_alive(run):
            status, active = "interrupted", 0
        if (
            status == "queued"
            and time.time() - run["updated_at"] > 5
            and not self._process_alive(run)
        ):
            status, active = "interrupted", 0
        elapsed = max(0, (run.get("finished_at") or time.time()) - run["started_at"])
        return {
            "run_id": run["id"],
            "kind": run["kind"],
            "status": status,
            "phase": run["phase"],
            "snapshot_id": run.get("snapshot_id"),
            "completed_batches": sum(b["status"] == "completed" for b in batches),
            "total_batches": len(batches),
            "active_workers": active,
            "failed_batches": sum(b["status"] == "failed" for b in batches),
            "reused_batches": sum(bool(b.get("reused")) for b in batches),
            "elapsed_seconds": round(elapsed, 1),
            "workers": run["config"]["workers"],
            "failures": [
                {"batch_id": b["id"], "error": b.get("error", "Worker failed")}
                for b in batches
                if b["status"] == "failed"
            ][:10],
            "error": run.get("error"),
            "reconciliation": run.get("reconciliation"),
            "export_error": run.get("export_error"),
            "evidence_error": run.get("evidence_error"),
            "record_exported": run.get("record_exported", False),
            "committed": publication.get("committed"),
            "pushed": publication.get("pushed"),
            "committed_pushed": publication.get("committed_pushed"),
            **attempt_summary(run),
            **timing_summary(run, batch_offset, batch_limit, include_details=include_details),
        }

    def _process_alive(self, run: dict[str, Any]) -> bool:
        if run.get("pid") == os.getpid():
            return (str(self.application.root), run["id"]) in _ACTIVE
        try:
            pid = run.get("pid", 0)
            if pid <= 0:
                return False
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OSError):
            return False
        with (self.directory / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return False
            except BlockingIOError:
                return True

    def status(
        self, run_id: str | None = None, *, batch_offset: int = 0, batch_limit: int = 10
    ) -> dict[str, Any]:
        if type(batch_offset) is not int or not 0 <= batch_offset <= 100_000:
            raise ValueError("batch_offset must be between 0 and 100000")
        if type(batch_limit) is not int or not 1 <= batch_limit <= 50:
            raise ValueError("batch_limit must be between 1 and 50")
        with _MUTEX:
            if run_id:
                return self._summary(
                    self._load(run_id), batch_offset=batch_offset, batch_limit=batch_limit
                )
            paths = sorted(
                self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
            )
            runs = [json.loads(p.read_text()) for p in paths if p.name != "settings.json"]
            publication = self._publication()
            return {
                "items": [self._summary(run, publication, include_details=False) for run in runs],
                "total": len(runs),
            }

    def start(
        self,
        kind: str,
        config: dict[str, Any] | None = None,
        scope: list[str] | None = None,
        *,
        background: bool = True,
    ) -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError("Workflow must be mapping, patterns, architecture, or fresh-eyes")
        with _MUTEX:
            active = [x for x in self.status()["items"] if x["status"] in {"running", "queued"}]
            if active:
                raise ValueError(f"Workflow {active[0]['run_id']} is already running")
            paths = sorted(
                (p for p in self.directory.glob("*.json") if p.name != "settings.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for old in paths[11:]:
                old.unlink()
            run: dict[str, Any] = {
                "id": uuid.uuid4().hex,
                "kind": KINDS[kind],
                "scope": scope,
                "config": _config(config) if config is not None else self.configuration(),
                "status": "queued",
                "phase": "Source scan",
                "started_at": time.time(),
                "batches": [],
                "stages_planned": [],
                "attempt_history_version": 1,
                "timing_version": 1,
                "architecture_scouts_version": 1,
                "pid": os.getpid(),
            }
            self._save(run)
        return self._launch(run["id"], background)

    def resume(self, run_id: str, *, background: bool = True) -> dict[str, Any]:
        with _MUTEX:
            run = self._load(run_id)
            if self._summary(run)["status"] == "completed":
                return self._summary(run)
            if self._summary(run)["status"] == "running":
                raise ValueError("This workflow is already running")
            run.pop("finished_at", None)
            run.pop("error", None)
            run["status"], run["pid"] = "queued", os.getpid()
            for batch in run["batches"]:
                if batch["status"] != "completed":
                    batch["status"] = "pending"
            self._save(run)
        return self._launch(run_id, background)

    def _launch(self, run_id: str, background: bool) -> dict[str, Any]:
        if not background:
            self._execute(run_id)
            return self.status(run_id)
        thread = threading.Thread(target=self._execute, args=(run_id,), daemon=True)
        with _MUTEX:
            _RUNNING[(str(self.application.root), run_id)] = thread
        thread.start()
        return self.status(run_id)

    def _execute(self, run_id: str) -> None:
        with (self.directory / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                with _MUTEX:
                    run = self._load(run_id)
                    run["status"], run["error"] = (
                        "failed",
                        "Another workflow worker owns this checkout",
                    )
                    self._save(run)
                return
            run = self._load(run_id)
            try:
                _ACTIVE.add((str(self.application.root), run_id))
                run["status"], run["pid"] = "running", os.getpid()
                self._save(run)
                scanned = self.application.scan()
                snapshot = scanned["snapshot"]["id"]
                if run.get("snapshot_id") and run["snapshot_id"] != snapshot:
                    raise ValueError(
                        "Checkout changed; start a new workflow for its current snapshot"
                    )
                run["snapshot_id"] = snapshot
                if (
                    run["kind"] == "patterns"
                    and "pattern_mode" not in run["config"]
                    and run["scope"] is None
                ):
                    # Old runs declared every module before production-only screening existed.
                    run["scope"] = [
                        node.id
                        for node in self.application._current().nodes
                        if node.kind == "module"
                    ]
                self._review(run)
                run["status"] = "completed"
            except Exception as error:
                run["status"], run["error"] = "failed", str(error)[:600]
            finally:
                # Accepted partial work is useful on another clone too. This runs
                # once per attempt, never once per annotation (which would be quadratic).
                try:
                    run["record_exported"] = bool(
                        self.application.export_record().get("record_exported")
                    )
                except Exception as error:
                    run["export_error"] = str(error)[:500]
                    if run["status"] == "completed":
                        run["status"], run["error"] = (
                            "failed",
                            "Analysis saved locally; portable export failed",
                        )
                run["finished_at"] = time.time()
                self._save(run)
                _RUNNING.pop((str(self.application.root), run_id), None)
                _ACTIVE.discard((str(self.application.root), run_id))

    def _review(self, run: dict[str, Any]) -> None:
        review_workflow(self, run)

    def _stage(self, run: dict[str, Any], kind: str | tuple[str, ...]) -> None:
        kinds = (kind,) if isinstance(kind, str) else kind
        labels = {
            "annotate": "Mapping annotations",
            "pattern-screen": "Fast catalogue screening",
            "patterns": "Catalogue review",
            "architecture": "Component architecture scouts",
            "fresh-eyes": "Independent subsystem designs",
        }
        run["phase"] = " / ".join(labels[stage] for stage in kinds)
        for stage in kinds:
            if stage not in run["stages_planned"]:
                if stage == "architecture":
                    plan_scouts(self, run)
                elif stage == "patterns" and run["config"].get("pattern_mode") == "screened":
                    self._plan_details(run)
                else:
                    self._plan_scopes(run, stage)
                run["stages_planned"].append(stage)
        self._save(run)
        pending = [b for b in run["batches"] if b["kind"] in kinds and b["status"] != "completed"]
        with ThreadPoolExecutor(max_workers=run["config"]["workers"]) as pool:
            jobs = [pool.submit(self._batch, run, batch) for batch in pending]
            for job in as_completed(jobs):
                job.result()

    def _plan_scopes(self, run, kind):
        graph = self.application._current()
        scope = run["scope"]
        if kind == "annotate" and run["kind"] in {"patterns", "architecture"} and scope is None:
            scope = [
                identity for group in plan_scopes(graph, "pattern-screen") for identity in group
            ]
            if not scope:
                return
        scopes = plan_scopes(
            graph,
            kind,
            scope,
            include_current=kind == "annotate" and bool(run["config"]["review_passes"]),
        )
        for scope in scopes:
            run["batches"].append(
                {
                    "id": len(run["batches"]) + 1,
                    "kind": kind,
                    "scope": scope,
                    "status": "pending",
                    "attempts": 0,
                }
            )

    def _plan_details(self, run):
        for batch in list(run["batches"]):
            if batch["kind"] == "pattern-screen":
                for selection in self.application.analysis_selection(batch["request_id"]):
                    run["batches"].append(
                        {
                            "id": len(run["batches"]) + 1,
                            "kind": "patterns",
                            "scope": [selection["module_id"]],
                            "screen_id": batch["request_id"],
                            "status": "pending",
                            "attempts": 0,
                        }
                    )

    def _request(self, batch):
        if "lens" in batch:
            return self.application.analysis_request(
                "architecture", batch["scope"], lens=batch["lens"]
            )
        if "children" in batch:
            return self.application.analysis_product_request(batch["children"])
        if "screen_id" in batch:
            selection = self.application.analysis_selection(batch["screen_id"])
            patterns = next(
                row["pattern_ids"] for row in selection if row["module_id"] in batch["scope"]
            )
            if not patterns:
                batch["status"], batch["skipped"] = "completed", True
                return None
            return self.application.analysis_request(
                "patterns", batch["scope"], pattern_ids=patterns, screened=True
            )
        return self.application.analysis_request(batch["kind"], batch["scope"])

    def _batch(self, run: dict[str, Any], batch: dict[str, Any]) -> None:
        with _MUTEX:
            started, calls_before = start_batch(batch)
            batch["status"], batch["attempts"] = "running", batch["attempts"] + 1
            batch.pop("error", None)
            self._save(run)
        phase, recorded = "request", False
        try:
            request = self._request(batch)
            if request is None:
                return
            with _MUTEX:
                batch["request_id"] = request["request_id"]
                self._save(run)
            choice = run["config"]["stages"][batch["kind"]]
            selected = _selected_worker(choice) | {
                "fallbacks": [_selected_worker(x) for x in choice.get("fallbacks", [])]
            }
            reviewers = [_selected_worker(x) for x in review_choices(run, batch["kind"])]
            pipeline = {"worker": selected, "review_passes": reviewers}
            if self.application.analysis_saved(request["request_id"], pipeline):
                self._record_call(run, batch, request, selected, "reused", 0, reused=True)
                with _MUTEX:
                    batch["status"], batch["reused"] = "completed", True
                    self._save(run)
                return
            candidates = [_worker(selected), *selected.get("fallbacks", [])]
            last_error = None
            for candidate in candidates:
                phase = "provider"
                try:
                    result = self._call(run, batch, request, candidate)
                    executed = applied_reviewers(request["kind"], result, reviewers)
                    for reviewer in executed:
                        result = self._call(run, batch, request, reviewer, draft=result)
                    phase = "validation"
                    execution = {
                        "pipeline": pipeline,
                        "worker": candidate,
                        "review_passes": executed,
                    }
                    result["execution"] = execution
                    for annotation in result.get("annotations", []):
                        annotation["execution"] = execution
                    self.application.submit_analysis(request["request_id"], result)
                    self._record_result(run, batch, request, candidate, "accepted")
                    recorded = False
                    break
                except Exception as error:
                    recorded = True
                    self._record_failure(run, batch, error, phase)
                    if phase == "validation":
                        self._record_result(run, batch, request, candidate, "rejected", error)
                    last_error = error
            else:
                raise ValueError(str(last_error))
            phase = "progress"
            with _MUTEX:
                batch["status"] = "completed"
                self._save(run)
        except Exception as error:
            if not recorded:
                self._record_failure(run, batch, error, phase)
            with _MUTEX:
                batch["status"], batch["error"] = "failed", _failure_message(error, phase)
                self._save(run)
        finally:
            with _MUTEX:
                finish_batch(batch, started, calls_before)
                self._save(run)

    def _record_failure(self, run, batch, error, phase):
        with _MUTEX:
            batch["failure_count"] = batch.get("failure_count", 0) + 1
            batch["attempt_failures"] = (
                batch.get("attempt_failures", [])
                + [
                    {
                        "attempt": batch["attempts"],
                        "phase": phase,
                        "error": _failure_message(error, phase),
                        "at": time.time(),
                    }
                ]
            )[-3:]
            self._save(run)

    def _record_result(self, run, batch, request, worker, status, error=None):
        evidence = getattr(self.application, "evidence", None)
        if evidence is None:
            return
        try:
            evidence.record(
                "analysis_result",
                {
                    "run_id": run["id"],
                    "batch_id": batch["id"],
                    "attempt": batch["attempts"],
                    "request_id": request["request_id"],
                    "stage": request["kind"],
                    "provider": worker["provider"],
                    "model": worker["model"],
                    "status": status,
                    "phase": "validation",
                    "notes": _failure_message(error, "validation") if error else None,
                },
            )
        except (ValueError, OSError) as failure:
            with _MUTEX:
                run["evidence_error"] = str(failure)[:300]

    def _call(self, run, batch, request, worker, *, draft=None):
        started = time.monotonic()
        try:
            result = self.provider(
                request,
                worker,
                timeout=run["config"]["timeout_seconds"],
                **({"draft": draft} if draft is not None else {}),
            )
        except Exception:
            self._record_call(
                run,
                batch,
                request,
                worker,
                "failed",
                time.monotonic() - started,
                review=draft is not None,
            )
            raise
        usage = result.pop("_anaxigraph_usage", None)
        self._record_call(
            run,
            batch,
            request,
            worker,
            "completed",
            time.monotonic() - started,
            usage=usage,
            review=draft is not None,
        )
        return result

    def _record_call(
        self,
        run,
        batch,
        request,
        worker,
        status,
        elapsed,
        *,
        usage=None,
        reused=False,
        review=False,
    ):
        evidence = getattr(self.application, "evidence", None)
        try:
            if evidence is not None:
                evidence.model_call(
                    worker["provider"],
                    worker["model"],
                    request["kind"] + (":review" if review else ""),
                    status,
                    elapsed,
                    usage=usage,
                    request_id=request["request_id"],
                    reused=reused,
                    attempt=batch["attempts"],
                    reasoning_effort=worker.get("reasoning_effort"),
                )
        except (ValueError, OSError) as error:
            # Do not pay for another model call because the separate ledger is full.
            with _MUTEX:
                run["evidence_error"] = str(error)[:300]
        try:
            with _MUTEX:
                record_model(
                    run, batch, request, worker, elapsed, usage, reused=reused, review=review
                )
                self._save(run)
        except (ValueError, OSError) as error:
            with _MUTEX:
                run["evidence_error"] = str(error)[:300]

    def _product(self, run: dict[str, Any]) -> None:
        children = [
            b["request_id"]
            for b in run["batches"]
            if b["kind"] == "fresh-eyes" and "children" not in b and b["status"] == "completed"
        ]
        level = 0
        while children:
            groups = [children[i : i + 16] for i in range(0, len(children), 16)]
            planned = []
            for group in groups:
                batch = next((b for b in run["batches"] if b.get("children") == group), None)
                if batch is None:
                    batch = {
                        "id": len(run["batches"]) + 1,
                        "kind": "fresh-eyes",
                        "children": group,
                        "scope": [],
                        "status": "pending",
                        "attempts": 0,
                    }
                    run["batches"].append(batch)
                planned.append(batch)
            self._save(run)
            with ThreadPoolExecutor(max_workers=run["config"]["workers"]) as pool:
                jobs = [
                    pool.submit(self._batch, run, batch)
                    for batch in planned
                    if batch["status"] != "completed"
                ]
                for job in as_completed(jobs):
                    job.result()
            next_level = []
            for batch in planned:
                if batch["status"] != "completed":
                    raise ValueError("Product reconciliation failed; subsystem designs are saved")
                next_level.append(batch["request_id"])
            if len(next_level) == 1:
                return
            children, level = next_level, level + 1
            if level > 8:
                raise ValueError("Product hierarchy exceeds the supported reconciliation depth")
