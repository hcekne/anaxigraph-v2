"""Build Snow Rankings with durable AnaxiGraph assistance and no task/model deadlines.

This is a new single-product build, not a timed matched-pair experiment. Coding and
whole-repository analysis occupy separate stable phases. The historical pair is unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import selectors
import sqlite3
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import run_pair
import trial
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MODEL, EFFORT = "gpt-6.1-sol", "high"
KINDS = ("mapping", "patterns", "fresh-eyes")


def command(root: Path, directory: Path, mcp_url: str) -> list[str]:
    args = ["codex"]
    for feature in run_pair.DISABLED:
        args += ["--disable", feature]
    settings = {
        "model_reasoning_effort": json.dumps(EFFORT),
        "approval_policy": '"never"',
        "project_doc_max_bytes": "0",
        "sandbox_workspace_write.network_access": "true",
        "sandbox_workspace_write.exclude_slash_tmp": "true",
        "sandbox_workspace_write.writable_roots": json.dumps([str(directory)]),
        "mcp_servers": "{}",
        "mcp_servers.anaxigraph.url": json.dumps(mcp_url),
        "mcp_servers.anaxigraph.required": "true",
        "mcp_servers.anaxigraph.default_tools_approval_mode": '"approve"',
    }
    for key, value in settings.items():
        args += ["--config", f"{key}={value}"]
    return args + [
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--json",
        "--model",
        MODEL,
        "--sandbox",
        "workspace-write",
        "--color",
        "never",
        "--cd",
        str(root),
        "--add-dir",
        str(directory),
        "-",
    ]


def coding(root, directory, mcp_url, prompt, progress, save) -> dict[str, Any]:
    """Stream until the coding process actually exits; never impose a task deadline."""
    directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    usage = dict.fromkeys(("input_tokens", "output_tokens", "cached_input_tokens"))
    process = subprocess.Popen(
        command(root, directory, mcp_url),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=root,
        env=run_pair.environment(directory),
        start_new_session=True,
    )
    progress.update(pid=process.pid, status="running", model=MODEL, reasoning_effort=EFFORT)
    save()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending, children, overflow = b"", {}, False
    completed, session, successes, failures = False, None, 0, 0
    tools: dict[str, int] = {}
    try:
        process.stdin.write(prompt.encode())
        process.stdin.close()
        while process.poll() is None or selector.get_map():
            children.update(run_pair.descendants(process.pid))
            for key, _ in selector.select(timeout=0.5):
                chunk = os.read(key.fileobj.fileno(), 65_536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if overflow:
                        overflow = False
                        continue
                    item = run_pair.extract(line)
                    if item is None:
                        continue
                    run_pair.event(directory / "events.jsonl", {"task": progress["task"]} | item)
                    if item.get("session_id"):
                        session = item["session_id"]
                    if item["type"] == "turn.completed":
                        completed = True
                        for name, amount in item["usage"].items():
                            if type(amount) is int and amount >= 0:
                                usage[name] = (usage[name] or 0) + amount
                    if item.get("server") == "anaxigraph" and "mcp_success" in item:
                        successes += int(item["mcp_success"])
                        failures += int(not item["mcp_success"])
                        if item["mcp_success"]:
                            name = item.get("tool", "unknown")
                            tools[name] = tools.get(name, 0) + 1
                    progress.update(
                        session_id=session,
                        last_activity_at=run_pair.now(),
                        last_item_type=item.get("item_type"),
                        mcp_successes=successes,
                        mcp_failures=failures,
                        elapsed_seconds=round(time.monotonic() - started, 1),
                        usage=dict(usage),
                        usage_basis="codex_turn_completed" if completed else None,
                        mcp_tools=dict(tools),
                    )
                    save()
                if len(pending) > 1_000_000:
                    pending, overflow = b"", True
        status = "completed" if process.returncode == 0 and completed else "failed"
    finally:
        selector.close()
        run_pair.stop_owned(process, children)
        process.stdout.close()
    return {
        "status": status,
        "model": MODEL,
        "reasoning_effort": EFFORT,
        "session_id": session,
        **usage,
        "cost_usd": None,
        "usage_basis": "codex_turn_completed"
        if any(v is not None for v in usage.values())
        else None,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "mcp_successes": successes,
        "mcp_failures": failures,
        "mcp_tools": tools,
    }


class Rpc:
    def __init__(self, session):
        self.session = session

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.session.call_tool(tool, arguments)
        if result.isError:
            raise RuntimeError(f"AnaxiGraph MCP tool {tool} reported failure")
        value = result.structuredContent
        if value is None:
            value = json.loads(next(item.text for item in result.content if item.type == "text"))
        if not isinstance(value, dict):
            raise RuntimeError(f"AnaxiGraph MCP tool {tool} returned an invalid result")
        return value


@asynccontextmanager
async def connect(url):
    async with httpx.AsyncClient(timeout=None) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=None) as session:
                await session.initialize()
                yield Rpc(session)


async def run_workflow(
    client,
    kind,
    progress,
    save,
    *,
    poll_seconds=5,
    max_failures=3,
) -> dict[str, Any]:
    """Wait without a deadline; resume failed batches, never rerun accepted batches."""
    tracking = progress.setdefault("workflows", {})
    previous = tracking.get(kind)
    status = await client.call(
        "workflow_status" if previous else "workflow_start",
        {"run_id": previous["run_id"]} if previous else {"kind": kind, "scope": None},
    )
    repeats, last_failure = 0, None
    while True:
        tracking[kind] = status
        save()
        if status["status"] == "completed":
            return status
        if status["status"] in {"failed", "interrupted"}:
            signature = json.dumps(
                [status.get("completed_batches"), status.get("failures"), status.get("error")],
                sort_keys=True,
            )
            repeats = repeats + 1 if signature == last_failure else 1
            last_failure = signature
            if repeats >= max_failures:
                raise RuntimeError(
                    f"{kind} workflow {status['run_id']} repeatedly failed without progress; "
                    "accepted batches remain saved and the build is incomplete"
                )
            status = await client.call("workflow_resume", {"run_id": status["run_id"]})
            continue
        if status["status"] not in {"queued", "running"}:
            raise RuntimeError(f"Unexpected {kind} workflow status: {status['status']}")
        await asyncio.sleep(poll_seconds)
        status = await client.call("workflow_status", {"run_id": status["run_id"]})


def ledger_rows(root: Path, *, after: int = 0):
    path = root / ".anaxigraph-v2/trial.sqlite"
    if not path.is_file():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        return [
            {"id": row[0], "task_id": row[1], "kind": row[2], "payload": json.loads(row[3])}
            for row in db.execute(
                "SELECT id,task_id,kind,payload FROM events WHERE id>? ORDER BY id", (after,)
            )
        ]


def sync_analysis(config, task: str) -> dict[str, Any]:
    """Import actual provider receipts once, retaining nulls and their provenance."""
    path = config.state / "trial.sqlite"
    previous = {
        row["payload"].get("notes") for row in trial.events(path) if row["kind"] == "analysis_call"
    }
    for row in ledger_rows(config.repo):
        if row["kind"] != "model_call":
            continue
        marker = f"server model_call receipt {row['id']}"
        if marker in previous:
            continue
        p = row["payload"]
        payload = {
            key: p.get(key)
            for key in (
                "provider",
                "model",
                "status",
                "elapsed_seconds",
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "cost_usd",
            )
        }
        payload.update(
            usage_basis=p.get("usage_source"),
            cost_basis=p.get("cost_source"),
            notes=marker,
        )
        trial.record(path, "treatment", task, "analysis_call", payload)
        previous.add(marker)
    rows = [row for row in trial.events(path) if row["kind"] == "analysis_call"]
    return {
        field: trial.metric(rows, field)
        for field in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "cost_usd",
            "elapsed_seconds",
        )
    }


async def preflight(config, client) -> dict[str, Any]:
    status = await client.call("status", {})
    if Path(status["root"]).resolve() != config.repo:
        raise RuntimeError("MCP service is connected to a different repository")
    before = max((row["id"] for row in ledger_rows(config.repo)), default=0)
    progress = {"task": "http-mcp-preflight"}
    directory = config.state / "preflight"
    directory.mkdir(parents=True, exist_ok=True)
    result = await asyncio.to_thread(
        coding,
        config.repo,
        directory,
        config.mcp_url,
        "Verify the supplied AnaxiGraph HTTP MCP service. Use MCP tools only: call scan, then "
        "prepare_change(task='Verify authorized Snow Rankings build access'), then "
        "assess_plan(task='Verify authorized Snow Rankings build access', "
        "files=['snow_rankings/app.py']). Do not use shell, edit files, or start workflows. "
        "Confirm only calls that actually succeeded.",
        progress,
        lambda: run_pair.atomic(directory / "progress.json", progress),
    )
    kinds = {row["kind"] for row in ledger_rows(config.repo, after=before)}
    passed = (
        result["status"] == "completed"
        and result.get("model") == MODEL
        and result.get("reasoning_effort") == EFFORT
        and result["mcp_failures"] == 0
        and {"scan", "prepare_change", "assess_plan"} <= result["mcp_tools"].keys()
        and {"task_started", "context_supplied"} <= kinds
    )
    proof = {
        "status": "passed" if passed else "failed",
        "at": run_pair.now(),
        "repo": str(config.repo),
        "mcp_url": config.mcp_url,
        "model": MODEL,
        "reasoning_effort": EFFORT,
        "coding": result,
        "server_events": sorted(kinds),
        "basis": "Real HTTP-configured Codex tool results plus independent server SQLite events",
    }
    progress.update(status=proof["status"], finished_at=run_pair.now())
    run_pair.atomic(directory / "progress.json", progress)
    run_pair.atomic(config.state / "preflight.json", proof)
    return proof


def checkpoint(config, label):
    if run_pair.git(config.repo, "status", "--porcelain"):
        run_pair.git(config.repo, "add", "-A")
        run_pair.git(config.repo, "commit", "-m", f"build: {label}")
    revision = run_pair.git(config.repo, "rev-parse", "HEAD")
    if config.publish_branch:
        run_pair.git(config.repo, "push", "origin", f"HEAD:refs/heads/{config.publish_branch}")
    return revision


def prompt_for(index, port, directory, previous=None, *, review=False):
    shared = (
        f"Work only in this checkout on Snow Rankings task{index}. Read AGENTS.md, "
        f"SPECIFICATION.md, API_CONTRACT.md, data/seed.json and tasks/{run_pair.TASKS[index - 1]}.md. "
        "Build maintainable working software; preserve the published contracts and earlier tasks. "
        "There is no task time limit. Do not inspect other implementations or spawn coding agents. "
        f"Use app localhost port {port} only; assigned temporary/cache paths are under {directory}. "
        "No paid accounts, bookings, credentials or public deployment. Write run.json as argv with "
        "literal {port}. Run meaningful checks and stop app servers you start. "
        "The coordinator handles Git commits and pushes; do not spend attempts on protected Git "
        "metadata. Keep tracked startup commands portable to other clones; use the assigned "
        "UV_CACHE_DIR environment instead of hard-coded machine paths in run.json. Use the "
        "supplied durable AnaxiGraph MCP service: scan, prepare_change, inspect "
        "relevant evidence, assess_plan before substantial edits, then scan/validate_change. "
        "Do not start, resume, configure or debug model workflows: the coordinator runs complete "
        "whole-repo workflows only while coding is stopped. "
    )
    if review:
        shared += (
            "The coordinator has completed whole-repository mapping, every module against the "
            "entire pattern catalogue, and reconciled Fresh Eyes. Discover saved reviews through "
            "reviews and inspect review:<id>, including the final product-level Fresh Eyes result. "
            "Consider alternative designs and the most useful pattern opportunities. Apply concrete "
            "improvements when justified, preserve behavior, and write docs/analysis-review-"
            f"{index:02}.md recording accepted/rejected suggestions and reasons, actual tests and "
            "remaining limitations. Avoid speculative abstractions. The coordinator will refresh "
            "all analyses on your final stable checkout if you change anything."
        )
    else:
        shared += "Implement this milestone only; later tasks are intentionally withheld."
    if previous:
        shared += "\nPrevious external acceptance: " + json.dumps(previous)
    return shared


async def analyse(config, client, entry, save):
    initial = await client.call("scan", {})
    snapshot = initial["snapshot"]["id"]
    saved_mapping = entry.get("workflows", {}).get("mapping", {})
    needs_mapping = initial["annotations"]["current"] != initial["modules"]
    if entry.get("snapshot_id") not in (None, snapshot) or (
        needs_mapping and saved_mapping.get("status") == "completed"
    ):
        entry.setdefault("superseded_runs", []).extend(
            value["run_id"] for value in entry.get("workflows", {}).values()
        )
        entry["workflows"] = {}
    entry["snapshot_id"] = snapshot
    for kind in KINDS:
        result = await run_workflow(client, kind, entry, save, poll_seconds=config.poll_seconds)
        reconciliation = result.get("reconciliation") or {}
        if reconciliation.get("status") != "completed":
            raise RuntimeError(f"{kind} lacks final reconciliation")
        if reconciliation.get("declared_modules") != initial["modules"]:
            raise RuntimeError(f"{kind} did not review every mapped module")
        if kind == "patterns":
            expected = reconciliation.get("expected_pairs")
            if not expected or reconciliation.get("evaluated_pairs") != expected:
                raise RuntimeError("Whole-repository pattern catalogue coverage is incomplete")
        if kind == "fresh-eyes" and not reconciliation.get("product_review_id"):
            raise RuntimeError("Fresh Eyes lacks a reconciled product-level design")
        if result.get("snapshot_id") != snapshot:
            raise RuntimeError("Checkout moved between stable analysis phases")
        sync_analysis(config, entry["task"])
    final = await client.call("status", {})
    if final["snapshot"]["id"] != snapshot or final["annotations"]["current"] != final["modules"]:
        raise RuntimeError("Whole-repository semantic mapping is incomplete or stale")
    entry["final_map"] = {
        "snapshot_id": snapshot,
        "modules": final["modules"],
        "annotations": final["annotations"],
    }
    entry["export"] = await client.call("export_record", {})
    entry["completed"] = True
    save()


async def coder(config, entry, save, *, review=False):
    directory = config.state / ("review" if review else "coding") / f"task-{entry['index']:02}"
    attempt = 0
    while True:
        attempt += 1
        entry["coding_attempt"] = attempt
        prompt = prompt_for(
            entry["index"], config.app_port, directory, entry.get("acceptance"), review=review
        )
        if attempt > 1 or run_pair.git(config.repo, "status", "--porcelain"):
            prompt += (
                "\nContinue the existing saved and uncommitted implementation. A previous session "
                "may have ended before completing this milestone. Inspect the current files and "
                "preserve useful work; finish the task and checks rather than starting over."
            )
        result = await asyncio.to_thread(
            coding, config.repo, directory, config.mcp_url, prompt, entry, save
        )
        record_coding(config, entry, result, review=review)
        if result["status"] == "completed" and result["mcp_successes"]:
            return result
        if result["status"] == "completed":
            raise RuntimeError("Coding completed without verified AnaxiGraph access")
        entry.setdefault("session_failures", []).append(
            {
                "session_id": result["session_id"],
                "status": result["status"],
                "elapsed_seconds": result["elapsed_seconds"],
                "kind": "provider_or_runtime_failure",
            }
        )
        entry["session_failures"] = entry["session_failures"][-20:]
        entry["status"] = "retrying_provider"
        save()
        # A transport/capacity failure must not discard work or end the build.
        # This is retry backoff, not a development or model deadline.
        await asyncio.sleep(min(60, 5 * attempt))


def record_coding(config, entry, result, *, review=False):
    if result.get("model") != MODEL or result.get("reasoning_effort") != EFFORT:
        raise ValueError("Coding receipt model/effort differs from this build")
    trial.record(
        config.state / "trial.sqlite",
        "treatment",
        entry["task"],
        "coding_call",
        {
            key: result[key]
            for key in (
                "status",
                "session_id",
                "elapsed_seconds",
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "cost_usd",
                "usage_basis",
            )
        }
        | {
            "model": MODEL,
            "reasoning_effort": EFFORT,
            "notes": "analysis review" if review else "development",
        },
    )


async def verify_and_repair(config, client, progress, entry, save):
    """Repair concrete acceptance failures before paying for architectural reviews."""
    repairs = entry.setdefault("repairs", [])
    previous_repairs = len(repairs)
    while True:
        progress["phase"] = f"task{entry['index']}: external acceptance"
        acceptance = await asyncio.to_thread(
            run_pair.verify,
            config.repo,
            config.app_port,
            entry["index"],
            config.state / "acceptance",
            2,
        )
        entry["acceptance"] = acceptance
        save()
        if acceptance["status"] == "passed":
            return len(repairs) != previous_repairs
        details = config.state / "acceptance" / f"acceptance-{entry['index']}.json"
        if details.is_file() and details.stat().st_size <= 24_000:
            entry["acceptance"] = acceptance | {"details": json.loads(details.read_text())}
        if len(repairs) >= 3:
            raise RuntimeError(
                f"Task{entry['index']} still fails after three repair attempts; "
                "the build remains incomplete and needs investigation"
            )
        progress["phase"] = f"task{entry['index']}: acceptance repair {len(repairs) + 1}"
        save()
        repairs.append(await coder(config, entry, save))
        checkpoint(config, f"task{entry['index']} acceptance repair {len(repairs)}")
        await client.call("scan", {})
        await client.call("validate_change", {})
        save()


async def rebind_publication(config, client, entry, save):
    """Rebind compatible saved evidence after a Git-only publication checkpoint."""
    status = await client.call("scan", {})
    if status["annotations"]["current"] != status["modules"]:
        raise RuntimeError("Publication left semantic annotations stale")
    entry["publication_snapshot_id"] = status["snapshot"]["id"]
    entry["publication_import"] = status.get("portable_import")
    entry["publication_export"] = await client.call("export_record", {})
    if entry["publication_export"].get("changed"):
        raise RuntimeError("Git-only publication unexpectedly changed the canonical record")
    modules, offset = set(), 0
    while True:
        page = await client.call("modules", {"limit": 100, "offset": offset})
        modules.update(row["id"] for row in page["items"])
        if page.get("next_offset") is None:
            break
        offset = page["next_offset"]
    pairs, product_ids = set(), []
    for kind in ("patterns", "fresh-eyes"):
        offset = 0
        while True:
            rows = await client.call("reviews", {"kind": kind, "limit": 100, "offset": offset})
            for row in rows["items"]:
                if row.get("status") != "current":
                    continue
                detail = await client.call("inspect", {"entity_id": f"review:{row['id']}"})
                body = detail["entity"]["data"]["result"]
                if kind == "patterns":
                    pairs.update(
                        (item["module_id"], item["pattern_id"])
                        for item in body["result"]["evaluations"]
                    )
                elif body.get("children") and set(body["scope"]) == modules:
                    product_ids.append(row["id"])
            if rows.get("next_offset") is None:
                break
            offset = rows["next_offset"]
    analysis = entry.get("final_analysis") or entry["initial_analysis"]
    expected = analysis["workflows"]["patterns"]["reconciliation"]["expected_pairs"]
    if len(pairs) != expected or {module for module, _ in pairs} != modules or not product_ids:
        raise RuntimeError("Published reviews do not cover the full catalogue and product")
    entry["publication_review_coverage"] = {
        "modules": len(modules),
        "evaluated_pairs": len(pairs),
        "expected_pairs": expected,
        "product_review_ids": product_ids,
    }
    save()


async def build(config, client, progress, save):
    previous = None
    for index in range(1, 5):
        task = run_pair.TASKS[index - 1]
        entry = progress["milestones"].setdefault(task, {"index": index, "task": task})
        if entry.get("completed"):
            previous = entry["acceptance"]
            continue
        run_pair.stage(config.repo, index)
        entry.setdefault("acceptance", previous)
        if "development" not in entry:
            progress["phase"] = f"task{index}: development"
            entry["development"] = await coder(config, entry, save)
            entry["development_checkpoint"] = checkpoint(config, f"task{index} implementation")
            save()
        await verify_and_repair(config, client, progress, entry, save)
        if index in (2, 4):
            if "review" not in entry:
                progress["phase"] = f"task{index}: whole-repository analyses"
                initial = entry.setdefault("initial_analysis", {"task": task})
                await analyse(config, client, initial, save)
                progress["phase"] = f"task{index}: evidence-based review"
                before = run_pair.git(config.repo, "rev-parse", "HEAD")
                entry["review"] = await coder(config, entry, save, review=True)
                after = checkpoint(config, f"task{index} reviewed design and analysis record")
                entry["review_changed"] = before != after
                save()
            if await verify_and_repair(config, client, progress, entry, save):
                entry["review_changed"] = True
            if entry["review_changed"]:
                progress["phase"] = f"task{index}: final stable analyses"
                final = entry.setdefault("final_analysis", {"task": task})
                await analyse(config, client, final, save)
        else:
            await client.call("scan", {})
            await client.call("validate_change", {})
        entry["publication_revision"] = checkpoint(
            config, f"task{index} verified analysis and evidence"
        )
        if index in (2, 4):
            await rebind_publication(config, client, entry, save)
        entry["completed"] = True
        entry["status"] = "completed"
        previous = entry["acceptance"]
        sync_analysis(config, task)
        save()


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo", type=Path, default=Path.home() / "repos/snow-rankings-v2")
    result.add_argument(
        "--state",
        type=Path,
        default=Path.home() / ".local/state/anaxigraph/experiments/snow-rankings-v2",
    )
    result.add_argument("--mcp-url", default="http://127.0.0.1:18782/mcp")
    result.add_argument("--app-port", type=int, default=18783)
    result.add_argument("--publish-branch", default="main")
    result.add_argument("--poll-seconds", type=float, default=5)
    result.add_argument("--preflight-only", action="store_true")
    result.add_argument("--preflight-proof", type=Path)
    result.add_argument("--dry-run", action="store_true")
    return result


async def main_async(config):
    config.repo, config.state = config.repo.resolve(), config.state.resolve()
    config.state.mkdir(parents=True, exist_ok=True)
    if config.dry_run:
        print(json.dumps(command(config.repo, config.state / "coding", config.mcp_url)))
        return 0
    lock = (config.state / "build.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    path = config.state / "progress.json"
    progress = (
        json.loads(path.read_text())
        if path.exists()
        else {
            "repo": str(config.repo),
            "model": MODEL,
            "reasoning_effort": EFFORT,
            "started_at": run_pair.now(),
            "milestones": {},
            "purpose": "Single-product unlimited build; no causal comparison with timed control",
        }
    )

    if progress.get("model") != MODEL or progress.get("reasoning_effort") != EFFORT:
        lock.close()
        raise RuntimeError("Existing build model/effort differs; use a new state directory")

    def save():
        progress["updated_at"] = run_pair.now()
        run_pair.atomic(path, progress)

    try:
        async with connect(config.mcp_url) as client:
            status = await client.call("status", {})
            if Path(status["root"]).resolve() != config.repo:
                raise RuntimeError("MCP service is connected to a different repository")
            if config.preflight_proof:
                proof = json.loads(config.preflight_proof.read_text())
                if not (
                    proof.get("status") == "passed"
                    and proof.get("repo") == str(config.repo)
                    and proof.get("mcp_url") == config.mcp_url
                    and proof.get("model") == MODEL
                    and proof.get("reasoning_effort") == EFFORT
                    and proof.get("coding", {}).get("model") == MODEL
                    and proof.get("coding", {}).get("reasoning_effort") == EFFORT
                    and proof.get("coding", {}).get("status") == "completed"
                    and proof.get("coding", {}).get("mcp_failures") == 0
                    and {"scan", "prepare_change", "assess_plan"}
                    <= proof.get("coding", {}).get("mcp_tools", {}).keys()
                    and {"task_started", "context_supplied"} <= set(proof.get("server_events", []))
                ):
                    raise RuntimeError("Preflight proof does not match this HTTP build")
            else:
                proof = await preflight(config, client)
            if proof["status"] != "passed":
                raise RuntimeError("Real HTTP MCP preflight failed; build not started")
            if config.preflight_only:
                print(json.dumps(proof))
                return 0
            progress.pop("error", None)
            progress.pop("error_type", None)
            progress.update(status="running", phase="starting")
            save()
            await build(config, client, progress, save)
            progress.update(status="completed", phase="finished", finished_at=run_pair.now())
    except Exception as error:
        progress.update(
            status="incomplete", error=str(error)[:600], error_type=type(error).__name__
        )
    finally:
        progress["analysis_usage"] = sync_analysis(config, "build-summary")
        save()
        lock.close()
    print(json.dumps({"status": progress["status"], "state": str(config.state)}))
    return int(progress["status"] != "completed")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parser().parse_args())))
