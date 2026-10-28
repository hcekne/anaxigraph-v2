"""Run paired advice/refactor/later-change trials, or an explicitly synthetic qualification.

No paid/model calls occur at import time. Live execution requires --live. Neither
preflight nor a model turn's completion establishes application correctness.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import random
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .evaluate import evaluate
    from .scenarios import SCENARIOS, brief, seed, task
except ImportError:
    from evaluate import evaluate
    from scenarios import SCENARIOS, brief, seed, task

MODEL, EFFORT = "gpt-6.1-sol", "xhigh"
STAGES = (
    "annotate",
    "pattern-screen",
    "patterns",
    "architecture",
    "delivery",
    "architecture-review",
    "delivery-review",
    "fresh-eyes",
)
DISABLED = ("memories", "apps", "plugins", "hooks", "multi_agent", "daemon_auto_start")


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False))
    temporary.replace(path)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text()
        for path in root.rglob("*")
        if path.is_file()
        and not any(
            part.startswith(".") or part == "__pycache__" for part in path.relative_to(root).parts
        )
        and path.name != "decisions.json"
        and str(path.relative_to(root)) != "anaxigraph/record.json"
    }


def delta(before, after):
    changed = sorted(
        path for path in before.keys() | after.keys() if before.get(path) != after.get(path)
    )
    # Churn is descriptive effort evidence, never an architectural score.
    import difflib

    changed_lines = sum(
        1
        for path in changed
        for line in difflib.ndiff(
            before.get(path, "").splitlines(), after.get(path, "").splitlines()
        )
        if line.startswith(("+ ", "- "))
    )
    return {"changed_paths": changed, "changed_lines_descriptive_only": changed_lines}


def ledger(root: Path) -> list[dict]:
    path = root / ".anaxigraph-v2" / "trial.sqlite"
    if not path.is_file():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        return [
            {"id": row[0], "kind": row[1], "payload": json.loads(row[2])}
            for row in db.execute("SELECT id,kind,payload FROM events ORDER BY id")
        ]


def command(root: Path, runtime: Path | None, agent="codex") -> list[str]:
    result = [agent]
    for feature in DISABLED:
        result += ["--disable", feature]
    settings = {
        "model_reasoning_effort": json.dumps(EFFORT),
        "approval_policy": '"never"',
        "project_doc_max_bytes": "0",
        "mcp_servers": "{}",
        "sandbox_workspace_write.network_access": "false",
    }
    if runtime is not None:
        settings.update(
            {
                "mcp_servers.anaxigraph.command": json.dumps(str(runtime)),
                "mcp_servers.anaxigraph.args": json.dumps(["--repo", str(root), "mcp"]),
                "mcp_servers.anaxigraph.required": "true",
                "mcp_servers.anaxigraph.default_tools_approval_mode": '"approve"',
                "mcp_servers.anaxigraph.tool_timeout_sec": "3600",
                "mcp_servers.anaxigraph.env.ANAXIGRAPH_EXPERIMENT_ARM": '"treatment"',
                "mcp_servers.anaxigraph.env.ANAXIGRAPH_EXPERIMENT_ID": json.dumps(root.parent.name),
            }
        )
    for key, value in settings.items():
        result += ["--config", f"{key}={value}"]
    return result + [
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--json",
        "--model",
        MODEL,
        "--sandbox",
        "workspace-write",
        "--color",
        "never",
        "--cd",
        str(root),
        "-",
    ]


def parsed_events(raw: str) -> list[dict]:
    events = []
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def tool_calls(events: list[dict]) -> list[dict]:
    result = []
    for event in events:
        item = event.get("item", {})
        if event.get("type") != "item.completed" or item.get("type") != "mcp_tool_call":
            continue
        reply = item.get("result")
        succeeded = (
            item.get("status") == "completed"
            and not item.get("error")
            and isinstance(reply, dict)
            and not (reply.get("isError") or reply.get("is_error"))
            and bool(
                reply.get("structuredContent")
                or reply.get("structured_content")
                or reply.get("content")
            )
        )
        body = (
            reply.get("structuredContent", reply.get("structured_content"))
            if isinstance(reply, dict)
            else None
        )
        if body is None and isinstance(reply, dict):
            for block in reply.get("content", []):
                if block.get("type") == "text":
                    try:
                        body = json.loads(block["text"])
                    except ValueError:
                        pass
        source = (
            body.get("entity", {}).get("data", {}).get("result") if isinstance(body, dict) else None
        )
        result.append(
            {
                "server": item.get("server"),
                "tool": item.get("tool"),
                "arguments": item.get("arguments", {}),
                "succeeded": succeeded,
                "result_digest": digest(reply) if succeeded else None,
                "source_digest": digest(source) if source is not None and succeeded else None,
            }
        )
    return result


def agent_call(root: Path, directory: Path, runtime: Path | None, prompt: str, config) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    for name in ("CODEX_THREAD_ID", "CLAUDECODE", "CLAUDE_SESSION_ID"):
        environment.pop(name, None)
    environment["ANAXIGRAPH_EXPERIMENT_ID"] = root.parent.name
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    started, started_at, status = time.monotonic(), now(), "failed"
    process = subprocess.Popen(
        command(root, runtime, config.agent),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=root,
        env=environment,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(prompt, timeout=config.call_timeout)
        status = "completed" if process.returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        status = "timed_out"
    (directory / "events.private.jsonl").write_text(stdout)
    (directory / "stderr.private.txt").write_text(stderr)
    events, usage = parsed_events(stdout), []
    for event in events:
        if event.get("type") == "turn.completed":
            usage.append(event.get("usage", {}))
    completed = any(event.get("type") == "turn.completed" for event in events)
    return {
        "status": status if completed else "failed",
        "started_at": started_at,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "session_ids": [
            event["thread_id"] for event in events if event.get("type") == "thread.started"
        ],
        "tools": tool_calls(events),
        "non_tool_work": any(
            event.get("item", {}).get("type") in {"command_execution", "file_change"}
            for event in events
        ),
        "tokens": {
            key: sum(row[key] for row in usage if type(row.get(key)) is int)
            if usage and all(type(row.get(key)) is int for row in usage)
            else None
            for key in ("input_tokens", "output_tokens", "cached_input_tokens")
        },
        "usage_basis": "Codex turn.completed usage; null if absent",
        "cost_usd": None,
        "cost_basis": "unknown; no billing receipt supplied",
        "model": MODEL,
        "reasoning_effort": EFFORT,
        "call_timeout": config.call_timeout,
        "final_message": "\n".join(
            event["item"].get("text", "")
            for event in events
            if event.get("item", {}).get("type") == "agent_message"
        ),
    }


def preflight(config) -> dict:
    attempt = getattr(config, "preflight_attempt", 0)
    directory = config.state / "preflight" / f"attempt-{attempt}"
    root = directory / "repo"
    root.mkdir(parents=True)
    (root / "probe.py").write_text("def available():\n    return True\n")
    before = snapshot(root)
    result = agent_call(
        root,
        directory / "client",
        config.runtime,
        "Use only AnaxiGraph MCP tools. Call scan, prepare_change(task='Architecture outcome "
        "qualification'), then inspect(entity_id='charter'). No shell, edits, or model workflows. "
        "Report failures honestly.",
        config,
    )
    calls = [row for row in result["tools"] if row["server"] == "anaxigraph"]
    kinds = {row["kind"] for row in ledger(root)}
    passed = (
        result["status"] == "completed"
        and not result["non_tool_work"]
        and snapshot(root) == before
        and {"scan", "prepare_change", "inspect"}
        <= {row["tool"] for row in calls if row["succeeded"]}
        and all(row["succeeded"] for row in calls)
        and {"task_started", "context_supplied"} <= kinds
    )
    proof = {
        "status": "passed" if passed else "failed",
        "evidence_kind": "synthetic" if getattr(config, "synthetic_transport", False) else "live",
        "runtime": str(config.runtime),
        "runtime_sha256": hashlib.sha256(config.runtime.read_bytes()).hexdigest(),
        "coding": result,
        "server_event_kinds": sorted(kinds),
        "at": now(),
    }
    write(config.state / "preflight.json", proof)
    write(directory / "proof.json", proof)
    return proof


def runtime_call(config, root, *args):
    result = subprocess.run(
        [str(config.runtime), "--repo", str(root), *args],
        capture_output=True,
        text=True,
        # A workflow contains many separately bounded worker calls. Its parent
        # process must not impose the same limit on the entire analysis phase.
        timeout=None if args[0] == "run" else config.call_timeout,
    )
    if result.returncode:
        raise RuntimeError(f"AnaxiGraph {args[0]} failed: {result.stderr[-1000:]}")
    return json.loads(result.stdout)


def request_packets(root: Path) -> list[dict]:
    from anaxigraph.storage import Store

    return [
        row["result"]
        for row in Store(root / ".anaxigraph-v2" / "knowledge.sqlite").reviews(str(root), "request")
        if row["status"] == "current" and row["result"].get("kind") == "fresh-eyes"
    ]


def workflow_configuration(config):
    return {
        "workers": config.analysis_workers,
        "timeout_seconds": config.call_timeout,
        "stages": {
            stage: {
                "provider": "codex",
                "model": MODEL,
                "reasoning_effort": EFFORT,
                "fallbacks": [],
            }
            for stage in STAGES
        },
    }


def freeze_advice(config, root: Path, destination: Path) -> dict:

    before, started = snapshot(root), time.monotonic()
    configuration = workflow_configuration(config)
    path = destination / "workflow-config.private.json"
    write(path, configuration)
    scanned = runtime_call(config, root, "scan")
    snapshot_id, module_count = scanned["snapshot"]["id"], scanned["modules"]
    workflows = {}
    for kind in ("mapping", "patterns", "fresh-eyes"):
        status = runtime_call(config, root, "run", kind, "--config", str(path))
        reconciliation = status.get("reconciliation") or {}
        if status.get("status") != "completed" or reconciliation.get("status") != "completed":
            raise RuntimeError(f"{kind} advice incomplete; no implementation may start")
        accounted = reconciliation.get("declared_modules", 0)
        if kind == "patterns":
            accounted += len(reconciliation.get("excluded_modules", []))
        if status.get("snapshot_id") != snapshot_id or accounted != module_count:
            raise RuntimeError(f"{kind} advice does not cover the exact complete baseline")
        if kind == "patterns" and (
            type(reconciliation.get("expected_pairs")) is not int
            or reconciliation.get("evaluated_pairs") != reconciliation["expected_pairs"]
        ):
            raise RuntimeError("Patterns catalogue coverage is incomplete")
        if kind == "fresh-eyes" and not reconciliation.get("product_review_id"):
            raise RuntimeError("Fresh Eyes lacks product synthesis")
        workflows[kind] = status
    reviews, offset = [], 0
    while True:
        page = runtime_call(config, root, "reviews", "--limit", "100", "--offset", str(offset))
        for row in page["items"]:
            if (
                row["kind"]
                in {"pattern-screen", "patterns", "architecture", "delivery", "fresh-eyes"}
                and row["status"] == "current"
            ):
                full = runtime_call(config, root, "inspect", "review:" + row["id"])
                if (
                    row["kind"] == "fresh-eyes"
                    and len(full["entity"]["data"]["result"]["result"].get("alternatives", [])) != 3
                ):
                    raise RuntimeError("Fresh Eyes did not retain three alternatives")
                reviews.append(
                    {
                        "id": row["id"],
                        "kind": row["kind"],
                        "result": full,
                        "source_digest": digest(full["entity"]["data"]["result"]),
                    }
                )
        if page.get("next_offset") is None:
            break
        offset = page["next_offset"]
    # Freeze actual delivered blind packets, not a claim of blindness in a prompt.
    packets = request_packets(root)
    if not packets or any(
        set(row["packet"]) - {"instruction", "requirements", "charter", "blindness", "level"}
        for row in packets
    ):
        raise RuntimeError("Fresh Eyes behavior-only packet audit failed")
    review_kinds = {row["kind"] for row in reviews}
    if (
        snapshot(root) != before
        or "fresh-eyes" not in review_kinds
        or not review_kinds.intersection({"patterns", "pattern-screen"})
    ):
        raise RuntimeError("Analysis changed the source or delivered no required advice")
    final = runtime_call(config, root, "status")
    if (
        final["snapshot"]["id"] != snapshot_id
        or final["annotations"]["current"] != final["modules"]
    ):
        raise RuntimeError("Frozen advice has stale or incomplete semantic mapping")
    result = {
        "at": now(),
        "baseline_digest": digest(before),
        "reviews": reviews,
        "blind_packets": packets,
        "workflows": workflows,
        "configuration": configuration,
        "quality_charter": packets[0]["packet"]["charter"],
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "model_receipts": [
            row
            for row in ledger(root)
            if row["kind"] == "model_call"
            and not row["payload"].get("reused")
            and row["payload"].get("status") != "reused"
        ],
        "cache_receipts": [
            row
            for row in ledger(root)
            if row["kind"] == "model_call"
            and (row["payload"].get("reused") or row["payload"].get("status") == "reused")
        ],
    }
    result["frozen_digest"] = digest(result)
    write(destination / "advice.private.json", result)
    return result


def dispositions(root: Path, advice: dict, patch: dict, calls: list[dict]) -> list[dict]:
    path = root / "decisions.json"
    try:
        notes = json.loads(path.read_text()) if path.is_file() else []
    except ValueError:
        notes = None
    if not isinstance(notes, list) or any(not isinstance(note, dict) for note in notes):
        return [
            {
                "error": "malformed disposition report",
                "source_reference_verified": False,
                "changed_source_evidence_verified": False,
            }
        ]
    frozen_ids = {review["id"]: review.get("source_digest") for review in advice["reviews"]}
    consulted = {
        row["arguments"].get("entity_id", "").removeprefix("review:")
        for row in calls
        if row["tool"] == "inspect"
        and row["succeeded"]
        and isinstance(row["arguments"], dict)
        and row.get("source_digest")
        == frozen_ids.get(row["arguments"].get("entity_id", "").removeprefix("review:"))
        and row.get("source_digest") is not None
    }
    result = []
    for note in notes:
        files = note.get("files", [])
        valid = (
            note.get("advice_id") in frozen_ids
            and note.get("advice_id") in consulted
            and note.get("disposition") in {"adopt", "defer", "reject"}
            and bool(note.get("reason"))
        )
        source_verified = valid and bool(files) and set(files) <= set(patch["changed_paths"])
        result.append(
            note
            | {
                "source_reference_verified": valid,
                "changed_source_evidence_verified": source_verified,
                "semantic_adoption": "requires blinded patch review; disposition is not proof",
            }
        )
    return result


def prompt_for(name, stage, advice):
    context = "No architectural review is supplied. Use your own judgment."
    if advice and advice.get("control_review"):
        context = (
            "Frozen independent generic architecture reviews are below. Consider their evidence "
            "and costs using your own judgment. These are ordinary source reviews without "
            "AnaxiGraph. Review records:\n" + json.dumps(advice["reviews"])
        )
    elif advice:
        context = (
            "Frozen pre-implementation advice is below. Inspect the cited saved reviews using "
            "AnaxiGraph MCP before deciding. Do not start new model workflows. Record material "
            "adopt/defer/reject dispositions in decisions.json as a list of {advice_id,disposition,"
            "reason,files}. A reasoned rejection is valid; adoption is not a goal. Frozen advice:\n"
            + json.dumps(advice["reviews"])
        )
    return (
        "You are a fresh implementation agent for one task. Do not commit, push, contact external "
        "services, read coordinator state outside this repository, or inspect hidden evaluations. "
        "Use stdlib only. Both arms use gpt-6.1-sol/xhigh, a fresh context and the same configured "
        "per-call timeout; there is no overall deadline or extra repair budget. Preserve earlier "
        "task behavior. Run your checks and report actual results and limitations.\n\n"
        + brief(name)
        + (
            "\nSource-independent quality charter:\n" + json.dumps(advice["quality_charter"])
            if advice and advice.get("quality_charter")
            else ""
        )
        + "\nEARLIER TASK REQUIREMENTS (preserve these):\n"
        + "\n".join(task(name, earlier) for earlier in range(stage))
        + "\nCURRENT TASK:\n"
        + task(name, stage)
        + "\n\n"
        + context
    )


def freeze_control_review(
    config, root: Path, destination: Path, name: str, opportunities: int, charter
) -> dict:
    before, reviews, receipts = snapshot(root), [], []
    lenses = (
        "responsibility and private boundaries",
        "behavioral contracts and error guarantees",
        "independent verification and CI input safety",
        "simplest alternatives and later change costs",
    )
    for index in range(opportunities):
        prompt = (
            "Review this small application without editing files. Use ordinary source inspection "
            "and grep. No AnaxiGraph or external services. Give concrete advice with source evidence, "
            "costs, independent behavioral checks and reasons to retain a simple design. Focus on "
            + lenses[index % len(lenses)]
            + ". You are a fresh reviewer. Future tasks are withheld.\n"
            + brief(name)
            + "\nSource-independent quality charter:\n"
            + json.dumps(charter)
            + "\nMilestone:\n"
            + task(name, 0)
        )
        receipt = agent_call(root, destination / f"control-review-{index}", None, prompt, config)
        if receipt["status"] != "completed" or snapshot(root) != before:
            raise RuntimeError("Read-only active control review failed or changed source")
        reviews.append(
            {"id": f"generic-{index}", "kind": "generic-review", "result": receipt["final_message"]}
        )
        receipts.append(receipt)
    result = {
        "control_review": True,
        "quality_charter": charter,
        "reviews": reviews,
        "model_receipts": receipts,
        "baseline_digest": digest(before),
        "opportunities": opportunities,
        "budget_basis": "matched number of model-call opportunities, not equal actual tokens",
    }
    result["frozen_digest"] = digest(result)
    write(destination / "control-advice.private.json", result)
    return result


def synthetic_step(root: Path, name: str, stage: int, treatment: bool):
    """Deterministic automation qualification only; never model/outcome evidence."""
    if stage == 0 and treatment:
        seed(root, name, modular=True)
    path = root / "compute.py"
    source = path.read_text()
    if stage == 1:
        extension = SCENARIOS[name]["extension"]
        default = 1 if name == "dispatch" else 0
        inserted = f"    extra = request.get('{extension}', {default})\n"
        inserted += (
            "    if type(extra) is not int or extra < "
            + str(default)
            + ":\n        raise ValueError('extension')\n"
        )
        if name == "dispatch":
            inserted += (
                "    if extra > 3:\n        raise ValueError('priority')\n    value *= extra\n"
            )
        elif name == "checkout":
            inserted += "    value = max(0, value - extra)\n"
        else:
            inserted += "    value = max(0, left - extra) * right\n"
        source = source.replace("    return ", inserted + "    return ", 1)
        path.write_text(source)
    elif stage == 2:
        path = root / "adapter.py"
        path.write_text(
            path.read_text().replace(
                "        return {'reference': reference, 'value': value,\n                'accepted': bool(dependency(reference, value))}",
                "        response = dependency(reference, value)\n        result = {'reference': reference, 'value': value,\n                  'accepted': response['ok'] if isinstance(response, dict) else bool(response)}\n        if isinstance(response, dict):\n            result['receipt'] = response['receipt']\n        return result",
            )
        )
    elif stage == 3:
        write(root / "settings.json", {"multiplier": 3})
        write(root / "schema.json", {"version": 2, "multiplier_semantics": "all_values"})


def run_arm(config, root, name, advice, arm, random_seed):
    rows = []
    for stage in range(config.stages):
        directory = root.parent / f"{arm}-evidence" / f"task-{stage}"
        before = snapshot(root)
        write(directory / "baseline.private.json", {"digest": digest(before), "files": before})
        if config.live:
            call = agent_call(
                root,
                directory,
                config.runtime if arm == "treatment" else None,
                prompt_for(name, stage, advice),
                config,
            )
        else:
            synthetic_step(root, name, stage, arm == "treatment")
            call = {
                "status": "completed",
                "evidence_kind": "synthetic",
                "tools": [],
                "elapsed_seconds": None,
                "tokens": None,
                "cost_usd": None,
            }
        patch = delta(before, snapshot(root))
        outcome = evaluate(
            root, name, stage, random_seed=random_seed + stage, changed_paths=patch["changed_paths"]
        )
        notes = (
            dispositions(root, advice, patch, call["tools"])
            if advice and arm == "treatment"
            else []
        )
        frozen = (
            {review["id"]: review.get("source_digest") for review in advice["reviews"]}
            if advice
            else {}
        )
        context_delivered = any(
            row["tool"] == "inspect"
            and row["succeeded"]
            and row["server"] == "anaxigraph"
            and isinstance(row["arguments"], dict)
            and row.get("source_digest") is not None
            and row["source_digest"]
            == frozen.get(row["arguments"].get("entity_id", "").removeprefix("review:"))
            for row in call["tools"]
        )
        row = {
            "stage": stage,
            "task": task(name, stage),
            "baseline_digest": digest(before),
            "coding": call,
            "patch": patch,
            "outcome": outcome,
            "dispositions": notes,
            "advice_consulted": context_delivered,
            "advice_frozen_digest": advice.get("frozen_digest") if advice else None,
        }
        write(directory / "result.private.json", row)
        rows.append(row)
        # Failure is retained with its actual budget. No arm gets an unpaired repair session.
    return rows


def measured_sum(values):
    known = [value for value in values if type(value) in (int, float)]
    return {
        "reported_sum": sum(known) if known else None,
        "known": len(known),
        "unknown": len(values) - len(known),
        "complete": bool(values) and len(known) == len(values),
    }


def comparison(manifest):
    pairs = []
    for pair in manifest["pairs"]:
        arms = {}
        for arm, rows in pair["arms"].items():
            coding = [row["coding"] for row in rows]
            analysis = [
                receipt.get("payload", receipt) for receipt in pair["analysis_model_receipts"][arm]
            ]
            arms[arm] = {
                "stages": [
                    {
                        "stage": row["stage"],
                        "behavior_passed": row["outcome"].get("behavior_passed", False),
                        "qualified": row["outcome"]["qualified"],
                        "ci_safe": row["outcome"].get("ci_safe", False),
                        "independent_components": row["outcome"].get("independent_components", 0),
                        "actual_evaluator_seconds": row["outcome"].get("actual_evaluator_seconds"),
                        "actual_candidate_check_seconds": row["outcome"].get(
                            "actual_candidate_check_seconds"
                        ),
                        "changed_source_paths": row["patch"]["changed_paths"],
                        "advice_consulted_source_verified": row["advice_consulted"],
                    }
                    for row in rows
                ],
                "coding": {
                    key: measured_sum(
                        [
                            call.get("tokens", {}).get(key)
                            if isinstance(call.get("tokens"), dict)
                            else None
                            for call in coding
                        ]
                    )
                    for key in ("input_tokens", "output_tokens", "cached_input_tokens")
                },
                "analysis": {},
            }
            for category, calls in (("coding", coding), ("analysis", analysis)):
                for key in ("elapsed_seconds", "cost_usd"):
                    arms[arm][category][key] = measured_sum([call.get(key) for call in calls])
                if category == "analysis":
                    for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
                        arms[arm][category][key] = measured_sum(
                            [
                                call.get(
                                    key,
                                    call.get("tokens", {}).get(key)
                                    if isinstance(call.get("tokens"), dict)
                                    else None,
                                )
                                for call in calls
                            ]
                        )
            arms[arm]["total_model_call_opportunities"] = len(coding) + len(analysis)
            arms[arm]["delivery_complete"] = (
                all(row["advice_consulted"] for row in rows)
                if arm == "treatment" and manifest["evidence_kind"] == "live"
                else None
            )
        pairs.append({"scenario": pair["scenario"], "replicate": pair["replicate"], "arms": arms})
    return {
        "evidence_kind": manifest["evidence_kind"],
        "control_mode": manifest["control_mode"],
        "status": manifest["status"],
        "pairs": pairs,
        "preparation_recoveries": manifest.get("preparation_recoveries", []),
        "administration": [
            {
                "kind": row["kind"],
                "recovery": row["recovery"],
                "coding": row["proof"].get("coding", {}),
            }
            for row in manifest.get("administration", [])
        ],
        "interpretation": "Compare later-task behavior, independence, CI safety and effort as separate paired outcomes. Synthetic qualification establishes evaluator sensitivity only.",
        "cost_efficiency_claim": None,
        "timing_caveat": "Candidate/evaluator timings are actual local subprocess measurements. CI unit estimates are synthetic and never reported as observed CI speedups.",
    }


def invocation(config):
    return {
        key: str(getattr(config, key)) if key == "runtime" else getattr(config, key)
        for key in (
            "runtime",
            "agent",
            "scenarios",
            "replicates",
            "stages",
            "call_timeout",
            "analysis_workers",
            "control_mode",
            "control_review_calls",
            "random_seed",
        )
    }


def source_hashes(root: Path):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def client_identity(config):
    resolved = shutil.which(config.agent)
    identity = {
        "requested": config.agent,
        "resolved_path": resolved,
        "available": resolved is not None,
        "version": None,
    }
    if not resolved:
        identity["error"] = "configured coding client is not installed"
    elif config.live and not config.synthetic_transport:
        try:
            reply = subprocess.run(
                [resolved, "--version"], capture_output=True, text=True, timeout=15
            )
            identity.update(
                available=reply.returncode == 0 and bool(reply.stdout.strip()),
                version=reply.stdout.strip(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            identity.update(available=False, error=type(error).__name__)
    else:
        identity["version"] = "synthetic/custom transport"
    return identity


def resume_preparation(config, previous):
    if not config.live or previous.get("status") != "incomplete" or previous.get("pairs"):
        raise ValueError(
            "Preparation recovery requires an incomplete live trial with no coding outcomes"
        )
    if list(config.state.glob("*/*-evidence/task-*/*")):
        raise ValueError("Coding preparation started; no extra coding opportunities are permitted")
    saved = json.loads((config.state / "invocation.private.json").read_text())
    if saved != invocation(config):
        raise ValueError("Preparation recovery configuration differs from the frozen invocation")
    proof = json.loads((config.state / "preflight.json").read_text())
    if (
        proof["runtime"] != str(config.runtime)
        or proof["runtime_sha256"] != hashlib.sha256(config.runtime.read_bytes()).hexdigest()
    ):
        raise ValueError("Preparation recovery runtime executable changed")
    provenance = json.loads((config.state / "runtime-source.private.json").read_text())
    source_root = Path(provenance.get("source_root", config.state / "runtime-source/anaxigraph"))
    if (
        not source_root.is_dir()
        or not provenance["files"]
        or source_hashes(source_root) != provenance["files"]
    ):
        raise ValueError("Preparation recovery runtime source changed or cannot be established")
    for replicate in range(config.replicates):
        for name in config.scenarios:
            pair_root = config.state / f"{name}-{replicate}"
            with tempfile.TemporaryDirectory() as temporary:
                expected = Path(temporary)
                seed(expected, name)
                for arm in ("control", "treatment"):
                    candidate = pair_root / arm
                    if candidate.exists() and snapshot(candidate) != snapshot(expected):
                        raise ValueError(
                            "Preparation recovery candidate source differs from the frozen seed"
                        )
            path = pair_root / "workflow-config.private.json"
            if path.exists() and json.loads(path.read_text()) != workflow_configuration(config):
                raise ValueError("Preparation recovery model workflow configuration changed")
            if list(pair_root.glob("control-review-*")):
                raise ValueError(
                    "Active control reviews already started; recovery would change review opportunities"
                )
    history = previous.setdefault("preparation_recoveries", [])
    index = len(history) + 1
    write(config.state / f"recovery-{index}.previous-run.private.json", previous)
    write(config.state / f"recovery-{index}.previous-preflight.private.json", proof)
    if not previous.get("administration"):
        previous["administration"] = [{"kind": "preflight", "proof": proof, "recovery": False}]
    history.append(
        {
            "at": now(),
            "prior_status": previous["status"],
            "prior_error_type": previous.get("error_type"),
            "prior_error": previous.get("error"),
            "basis": "same executable, source, seed and configuration; no coding started; accepted analyses reused",
        }
    )
    config.preflight_attempt = index
    previous.update(status="qualifying")
    previous.pop("error", None)
    previous.pop("error_type", None)
    return previous


def run_trial(config):
    config.state = config.state.resolve()
    if config.runtime:
        config.runtime = config.runtime.resolve()
    # Custom clients are useful for transport qualification, but must never be
    # presented as evidence from the real Codex client/model.
    config.synthetic_transport = (
        getattr(config, "synthetic_transport", False) or config.agent != "codex"
    )
    config.state.mkdir(parents=True, exist_ok=True)
    previous = None
    if (config.state / "run.private.json").exists() and getattr(
        config, "resume_preparation", False
    ):
        previous = resume_preparation(
            config, json.loads((config.state / "run.private.json").read_text())
        )
        config._recovery_started = True
    elif (config.state / "run.private.json").exists():
        raise ValueError("Use a new state directory; frozen trials are never silently resumed")
    manifest = {
        "started_at": now(),
        "evidence_kind": "live"
        if config.live and not getattr(config, "synthetic_transport", False)
        else "synthetic",
        "model": MODEL,
        "reasoning_effort": EFFORT,
        "call_timeout": config.call_timeout,
        "overall_deadline": None,
        "scenarios": config.scenarios,
        "replicates": config.replicates,
        "control_mode": config.control_mode,
        "client_identity": client_identity(config),
        "benchmark_source_sha256": digest(source_hashes(Path(__file__).parent)),
        "budget_basis": "equal coding opportunities; analysis costs reported separately; no equal-total-token claim",
        "status": "qualifying",
        "pairs": [],
    }
    if previous is not None:
        manifest = previous
        manifest.setdefault("coordinator_revisions", []).append(
            {"at": now(), "benchmark_source_sha256": digest(source_hashes(Path(__file__).parent))}
        )
    else:
        write(config.state / "invocation.private.json", invocation(config))
        if config.runtime_source_root:
            source_root = config.runtime_source_root.resolve()
            write(
                config.state / "runtime-source.private.json",
                {"source_root": str(source_root), "files": source_hashes(source_root)},
            )
    write(config.state / "run.private.json", manifest)
    if config.live:
        if not manifest["client_identity"]["available"]:
            manifest.update(status="preflight_failed", error="coding client unavailable")
            write(config.state / "run.private.json", manifest)
            return manifest
        if not config.runtime or not config.runtime.is_file():
            raise ValueError("Live runs require an explicit installed AnaxiGraph executable")
        proof = preflight(config)
        manifest.setdefault("administration", []).append(
            {"kind": "preflight", "proof": proof, "recovery": previous is not None}
        )
        if (
            proof["status"] != "passed"
            or proof.get("evidence_kind", "live") != manifest["evidence_kind"]
        ):
            manifest["status"] = "preflight_failed"
            write(config.state / "run.private.json", manifest)
            return manifest
        if config.preflight_only:
            manifest["status"] = "preflight_passed_no_trial"
            write(config.state / "run.private.json", manifest)
            return manifest
    rng = random.Random(config.random_seed)
    blind_map = {}
    for replicate in range(config.replicates):
        for name in config.scenarios:
            pair_root = config.state / f"{name}-{replicate}"
            roots = {arm: pair_root / arm for arm in ("control", "treatment")}
            for root in roots.values():
                if not root.exists():
                    seed(root, name)
            baseline = digest(snapshot(roots["control"]))
            assert baseline == digest(snapshot(roots["treatment"]))
            advice = (
                freeze_advice(config, roots["treatment"], pair_root)
                if config.live
                else {
                    "reviews": [],
                    "frozen_digest": "synthetic-not-live-advice",
                    "baseline_digest": baseline,
                }
            )
            control_advice = None
            if config.live and config.control_mode == "review":
                opportunities = config.control_review_calls or len(advice["model_receipts"])
                if not opportunities:
                    raise RuntimeError(
                        "Cannot match active review opportunities without actual model receipts"
                    )
                control_advice = freeze_control_review(
                    config,
                    roots["control"],
                    pair_root,
                    name,
                    opportunities,
                    advice["quality_charter"],
                )
            sample_seed = rng.randrange(2**32)
            order = list(roots)
            rng.shuffle(order)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = {
                    arm: pool.submit(
                        run_arm,
                        config,
                        roots[arm],
                        name,
                        advice if arm == "treatment" else control_advice,
                        arm,
                        sample_seed,
                    )
                    for arm in order
                }
                arms = {arm: futures[arm].result() for arm in order}
            pair = {
                "scenario": name,
                "replicate": replicate,
                "starting_digest": baseline,
                "hidden_sample_seed": sample_seed,
                "arms": arms,
                "analysis_model_receipts": {
                    "treatment": advice.get("model_receipts", []),
                    "control": control_advice.get("model_receipts", []) if control_advice else [],
                },
            }
            manifest["pairs"].append(pair)
            for arm in order:
                label = f"candidate-{rng.getrandbits(64):016x}"
                blind_map[label] = {"scenario": name, "replicate": replicate, "arm": arm}
                destination = config.state / "blind-review" / label
                destination.mkdir(parents=True)
                for relative, content in snapshot(roots[arm]).items():
                    output = destination / relative
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(content)
                # Exclude advice, agent claims and arm labels from the reviewer view.
                write(destination / "outcomes.json", [row["outcome"] for row in arms[arm]])
                write(
                    destination / "tasks.json",
                    [
                        {
                            "task": row["task"],
                            "patch": row["patch"],
                            "outcome": row["outcome"],
                            "coding_elapsed_seconds": row["coding"]["elapsed_seconds"],
                            "coding_tokens": row["coding"]["tokens"],
                        }
                        for row in arms[arm]
                    ],
                )
            write(config.state / "run.private.json", manifest)
    manifest.update(status="completed", finished_at=now())
    manifest["inference"] = (
        "Descriptive paired outcomes only; no causal win claimed from qualification or a single pair"
    )
    write(config.state / "blind-map.private.json", blind_map)
    write(config.state / "run.private.json", manifest)
    write(config.state / "comparison.json", comparison(manifest))
    return manifest


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--synthetic", action="store_true")
    result.add_argument("--state", type=Path, required=True)
    result.add_argument("--runtime", type=Path)
    result.add_argument("--agent", default="codex")
    result.add_argument("--scenarios", nargs="+", choices=SCENARIOS, default=list(SCENARIOS))
    result.add_argument("--replicates", type=int, default=1)
    result.add_argument("--stages", type=int, choices=range(1, 5), default=4)
    result.add_argument(
        "--call-timeout", type=int, default=None, help="Per call seconds; default no deadline"
    )
    result.add_argument("--analysis-workers", type=int, default=4)
    result.add_argument("--control-mode", choices=("normal", "review"), default="normal")
    result.add_argument(
        "--control-review-calls",
        type=int,
        help="Optional predeclared override; otherwise match observed analysis calls",
    )
    result.add_argument("--random-seed", type=int, default=20261028)
    result.add_argument("--preflight-only", action="store_true")
    result.add_argument(
        "--runtime-source-root",
        type=Path,
        help="Record exact installed runtime source for guarded preparation recovery",
    )
    result.add_argument(
        "--resume-preparation",
        action="store_true",
        help="Explicitly recover failed advice preparation before any coding; no extra coding turns",
    )
    return result


def main():
    config = parser().parse_args()
    if config.replicates < 1 or (
        config.call_timeout is not None and not 30 <= config.call_timeout <= 3600
    ):
        raise ValueError("replicates >=1; call-timeout must be omitted or 30..3600")
    if config.control_review_calls is not None and config.control_review_calls < 1:
        raise ValueError("control-review-calls must be positive")
    existing_manifest = (config.state.resolve() / "run.private.json").is_file()
    try:
        result = run_trial(config)
    except Exception as error:
        path = config.state.resolve() / "run.private.json"
        if path.is_file() and (
            not existing_manifest or getattr(config, "_recovery_started", False)
        ):
            result = json.loads(path.read_text())
            # Preserve completed per-task evidence and the first failing condition.
            result.update(
                status="incomplete", error_type=type(error).__name__, error=str(error)[:1000]
            )
            write(path, result)
        elif existing_manifest:
            print(json.dumps({"status": "refused_existing_state", "state": str(config.state)}))
            return 1
        else:
            raise
        print(
            json.dumps(
                {
                    "status": "incomplete",
                    "state": str(config.state),
                    "error_type": type(error).__name__,
                }
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": result["status"],
                "evidence_kind": result["evidence_kind"],
                "state": str(config.state),
            }
        )
    )
    return 0 if result["status"] in {"completed", "preflight_passed_no_trial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
