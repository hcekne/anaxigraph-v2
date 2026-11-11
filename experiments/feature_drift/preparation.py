"""Retain model preparation and stop before coding if the treatment was not delivered."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path


def item(payload):
    return payload.get("items", [payload])[0] if "items" in payload else payload


def qualified(kind: str, result: dict, progress: dict) -> bool:
    required = {
        "mapping": {"annotate"},
        "patterns": {"annotate", "pattern-screen", "patterns", "architecture", "delivery"},
        "architecture": {"annotate", "architecture"},
        "ci": {"delivery"},
        "fresh-eyes": {"annotate", "fresh-eyes"},
    }[kind]
    if kind == "patterns" and progress.get("config", {}).get("pattern_mode") == "exhaustive":
        required.remove("pattern-screen")
    return (
        result.get("status") == "completed"
        and result.get("failed_batches") == 0
        and result.get("active_workers") == 0
        and type(result.get("total_batches")) is int
        and result.get("completed_batches") == result.get("total_batches")
        and not any(
            result.get(key)
            for key in ("error", "export_error", "evidence_error", "failure_history_error")
        )
        and required <= set(progress.get("stages_planned", []))
        and isinstance(progress.get("reconciliation"), dict)
    )


def refresh(config, repo: Path, step, directory: Path, runtime, summary, write_json):
    """Resume the same failed workflow, keeping successful batches and every receipt.

    A receipt interrupted before its response is retained for operator recovery,
    never silently overwritten or retried while its worker could still be alive.
    """
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "refresh.json"
    report = json.loads(manifest.read_text()) if manifest.exists() else {"seconds": 0.0}
    attempts = sorted(directory.glob("attempt-*.json"))
    if any("finished" not in json.loads(path.read_text()) for path in attempts):
        return report | {
            "qualified": False,
            "error": "Interrupted preparation receipt; inspect its saved workflow before recovery",
        }
    if len(attempts) != report.get("attempts", 0):
        return report | {
            "qualified": False,
            "error": "Preparation receipt/manifest mismatch; recover the retained response and cost before retrying",
            "retained_attempt_seconds": sum(
                json.loads(path.read_text())["seconds"] for path in attempts
            ),
        }
    if any(json.loads(path.read_text()).get("unknown_worker") for path in attempts):
        return report | {
            "qualified": False,
            "error": "Preparation worker lifetime is unknown; inspect retained progress before recovery",
        }
    extra = ["--config", str(config.workflow_config)] if config.workflow_config else []
    kinds = ["mapping"] + (list(config.review_kinds) if step in config.review_steps else [])
    for kind in ["scan", *kinds]:
        if kind != "scan" and report.get(kind, {}).get("qualified"):
            continue
        previous = report.get(kind, {}) if kind != "scan" else {}
        run_id = previous.get("run_id")
        command = (
            ["scan"] if kind == "scan" else ["resume", run_id] if run_id else ["run", kind, *extra]
        )
        path = directory / f"attempt-{len(attempts):03d}.json"
        receipt = {"kind": kind, "command": command, "started": time.time()}
        write_json(path, receipt)
        attempts.append(path)
        started = time.monotonic()
        try:
            payload = runtime(config, repo, *command)
            result = item(payload)
            receipt["response"] = payload
            if kind == "scan":
                snapshot = (result.get("snapshot") or {}).get("id")
                valid = bool(snapshot) and not result.get("error")
                if report.get("snapshot_id") not in (None, snapshot):
                    raise ValueError("Checkout changed during unfinished model preparation")
                report.update(scan=valid, snapshot_id=snapshot)
            else:
                run_id = result.get("run_id")
                progress = {}
                if isinstance(run_id, str) and re.fullmatch(r"[0-9a-f]{32}", run_id):
                    saved = repo / ".anaxigraph-v2" / "workflows" / f"{run_id}.json"
                    if saved.is_file():
                        progress = json.loads(saved.read_text())
                        receipt["workflow"] = progress
                receipt["unknown_worker"] = result.get("status") not in {
                    "completed",
                    "failed",
                } or progress.get("status") not in {"completed", "failed"}
                valid = qualified(kind, result, progress)
                report[kind] = summary(result) | {"run_id": run_id, "qualified": valid}
        except Exception as error:
            valid = False
            receipt["error"] = f"{type(error).__name__}: {error}"
            receipt["unknown_worker"] = kind != "scan"
        receipt.update(finished=time.time(), seconds=time.monotonic() - started, qualified=valid)
        report["seconds"] += receipt["seconds"]
        report["attempts"] = len(attempts)
        report["qualified"] = valid and kind == kinds[-1]
        report["error"] = (
            receipt.get("error")
            if valid
            else receipt.get(
                "error",
                f"{kind} did not complete; resume the same command after resolving its saved failure",
            )
        )
        write_json(path, receipt)
        write_json(manifest, report)
        if not valid:
            break
    report["qualified"] = bool(
        valid
        and report.get("scan")
        and all(report.get(kind, {}).get("qualified") for kind in kinds)
    )
    write_json(manifest, report)
    return report
