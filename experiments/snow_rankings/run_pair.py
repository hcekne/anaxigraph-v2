"""Run the matched Snow Rankings coding tasks in parallel, retaining only bounded metrics.

No AnaxiGraph runtime is imported here. The treatment gets one explicitly configured
MCP process; the control gets no MCP servers. Each task starts a fresh coding context.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import json
import os
import selectors
import shutil
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import acceptance
import trial

HERE = Path(__file__).resolve().parent
DEFAULT_STATE = Path.home() / ".local/state/anaxigraph/experiments/snow-rankings-2026-10-26"
TASKS = ("01-foundation", "02-ranking", "03-resilience", "04-maintenance")
DISABLED = ("memories", "apps", "plugins", "hooks", "multi_agent", "daemon_auto_start")
LOG_LIMIT = 4_000_000


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic(path: Path, value: Any) -> None:
    raw = json.dumps(value, indent=2, allow_nan=False).encode()
    if len(raw) > 128_000:
        raise ValueError("Progress exceeds its bounded size")
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_bytes(raw)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True, text=True, timeout=45, check=True
    )
    return result.stdout.strip()


def environment(directory: Path) -> dict[str, str]:
    result = dict(os.environ)
    for key in ("CODEX_THREAD_ID", "CLAUDECODE", "CLAUDE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT"):
        result.pop(key, None)
    for name in ("tmp", "cache", "uv-cache"):
        (directory / name).mkdir(parents=True, exist_ok=True)
    result.update(
        {
            "TMPDIR": str(directory / "tmp"),
            "XDG_CACHE_HOME": str(directory / "cache"),
            "UV_CACHE_DIR": str(directory / "uv-cache"),
            "PYTHONUNBUFFERED": "1",
        }
    )
    return result


def command(root: Path, arm: str, directory: Path, runtime: Path) -> list[str]:
    args = ["codex"]
    for feature in DISABLED:
        args += ["--disable", feature]
    settings = {
        "model_reasoning_effort": '"xhigh"',
        "approval_policy": '"never"',
        "project_doc_max_bytes": "0",
        "sandbox_workspace_write.network_access": "true",
        "sandbox_workspace_write.exclude_slash_tmp": "true",
        "sandbox_workspace_write.writable_roots": json.dumps([str(directory)]),
        "mcp_servers": "{}",
    }
    if arm == "treatment":
        settings.update(
            {
                "mcp_servers.anaxigraph.command": json.dumps(str(runtime)),
                "mcp_servers.anaxigraph.args": json.dumps(["--repo", str(root), "mcp"]),
                "mcp_servers.anaxigraph.cwd": json.dumps(str(root)),
                "mcp_servers.anaxigraph.required": "true",
                "mcp_servers.anaxigraph.startup_timeout_sec": "60",
                "mcp_servers.anaxigraph.tool_timeout_sec": "1100",
                "mcp_servers.anaxigraph.env.ANAXIGRAPH_EXPERIMENT_ARM": '"treatment"',
                "mcp_servers.anaxigraph.env.ANAXIGRAPH_EXPERIMENT_ID": '"snow-rankings-2026-10-26"',
            }
        )
    for key, value in settings.items():
        args += ["--config", f"{key}={value}"]
    args += [
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--json",
        "--model",
        "gpt-6.1-sol",
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
    return args


def process_start(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def descendants(pid: int) -> dict[int, str]:
    found: dict[int, str] = {}
    pending = [pid]
    while pending:
        parent = pending.pop()
        try:
            children = Path(f"/proc/{parent}/task/{parent}/children").read_text().split()
        except OSError:
            continue
        for child in children:
            identity = int(child)
            started = process_start(identity)
            if started and identity not in found:
                found[identity] = started
                pending.append(identity)
    return found


def stop_owned(process: subprocess.Popen, children: dict[int, str] | None = None) -> None:
    owned = dict(children or {}) | descendants(process.pid)
    started = process_start(process.pid)
    if started and process.poll() is None:
        owned[process.pid] = started
    for signum in (signal.SIGTERM, signal.SIGKILL):
        for pid, original in owned.items():
            if process_start(pid) == original:
                try:
                    os.kill(pid, signum)
                except ProcessLookupError:
                    pass
        if not any(process_start(pid) == original for pid, original in owned.items()):
            break
        if signum == signal.SIGTERM:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass


def event(path: Path, data: dict[str, Any]) -> None:
    raw = (json.dumps({"at": now()} | data, allow_nan=False) + "\n").encode()
    if path.exists() and path.stat().st_size + len(raw) > LOG_LIMIT:
        return  # The SQLite aggregate retains task/usage outcomes; raw progress is bounded.
    with path.open("ab") as stream:
        stream.write(raw)


def extract(line: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(value, dict):
        return None
    result = {"type": value.get("type", "unknown")}
    if result["type"] == "thread.started":
        result["session_id"] = value.get("thread_id")
    if result["type"] == "turn.completed":
        usage = value.get("usage", {})
        result["usage"] = {
            key: usage.get(key) for key in ("input_tokens", "output_tokens", "cached_input_tokens")
        }
    item = value.get("item", {})
    if isinstance(item, dict):
        result["item_type"] = item.get("type")
        for key in ("status", "exit_code", "server", "tool"):
            if key in item and isinstance(item[key], (str, int)):
                result[key] = item[key]
    if result["type"] in {"error", "turn.failed"}:
        result["error_category"] = "provider_or_runtime_error"
    return result


def coding(
    root: Path,
    arm: str,
    directory: Path,
    runtime: Path,
    prompt: str,
    deadline: float,
    progress: dict[str, Any],
    save,
) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
    }
    session, status, attempts = None, "failed", 0
    while time.monotonic() < deadline and attempts < 2:
        attempts += 1
        process = subprocess.Popen(
            command(root, arm, directory, runtime),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=root,
            env=environment(directory),
            start_new_session=True,
        )
        progress.update(
            {"pid": process.pid, "attempt": attempts, "phase": "coding", "status": "running"}
        )
        save()
        process.stdin.write(prompt.encode())
        process.stdin.close()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        pending, children = b"", {}
        completed_turn, saw_work, overflow = False, False, False
        try:
            while time.monotonic() < deadline:
                children.update(descendants(process.pid))
                ready = selector.select(timeout=0.5)
                for key, _ in ready:
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
                        item = extract(line)
                        if item is None:
                            continue
                        event(
                            directory / "events.jsonl",
                            {"task": progress["task"], "attempt": attempts} | item,
                        )
                        if item.get("session_id"):
                            session = item["session_id"]
                            progress["session_id"] = session
                            save()
                        if item.get("item_type") in {
                            "command_execution",
                            "mcp_tool_call",
                            "file_change",
                        }:
                            saw_work = True
                            progress["last_activity_at"] = now()
                            progress["last_item_type"] = item["item_type"]
                            save()
                        if item["type"] == "turn.completed":
                            completed_turn = True
                            for name, amount in item["usage"].items():
                                if type(amount) is int and amount >= 0:
                                    usage[name] = (usage[name] or 0) + amount
                        if item.get("server") == "anaxigraph" and item.get("tool") == "inspect":
                            trial.record(
                                directory.parent / "trial.sqlite",
                                arm,
                                progress["task"],
                                "context_consulted",
                                {"context_ids": ["anaxigraph.inspect"], "session_id": session},
                            )
                    if len(pending) > 1_000_000:
                        pending, overflow = b"", True
                if process.poll() is not None and not selector.get_map():
                    break
            if process.poll() is None:
                status = "timed_out"
            elif process.returncode == 0 and completed_turn:
                status = "completed"
            else:
                status = "failed"
        finally:
            selector.close()
            stop_owned(process, children)
            process.stdout.close()
        if status == "completed" or saw_work or status == "timed_out":
            break
        # Retry only an early provider/runtime failure, inside the same task deadline.
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    return {
        "status": status,
        "session_id": session,
        "attempts": attempts,
        **usage,
        "usage_basis": "codex_turn_completed"
        if any(v is not None for v in usage.values())
        else None,
        "cost_usd": None,
    }


def stage(root: Path, index: int) -> None:
    if index > 1:
        target = root / "tasks" / f"{TASKS[index - 1]}.md"
        target.write_bytes((HERE / "tasks" / target.name).read_bytes())
    if index == 4:
        contract = root / "API_CONTRACT.md"
        if "Milestone4 adds" not in contract.read_text():
            paragraph = (HERE / "api-contract.md").read_text().split("Milestone4 adds", 1)[1]
            with contract.open("a") as stream:
                stream.write("\nMilestone4 adds" + paragraph)


def prompt_for(arm: str, index: int, port: int, directory: Path, previous: dict | None) -> str:
    text = (
        f"Implement task {index}: read AGENTS.md, SPECIFICATION.md, API_CONTRACT.md, data/seed.json "
        f"and tasks/{TASKS[index - 1]}.md in this checkout. Follow them. Do not inspect other "
        "implementations or unrelated files, and do not spawn coding subagents. Build good maintainable "
        "software. Work only on the current task; later tasks are intentionally withheld. "
        f"Use localhost port {port} only. Temporary/cache paths are assigned under {directory}. "
        "No paid accounts, credentials, bookings or public deployment. Write run.json with "
        '{"command":["uv","run",...]}; it must start this app with literal {port} in argv. '
        "Run meaningful tests and preserve prior contracts. Commit your milestone if permitted; "
        "the coordinator can commit/push if Git metadata is sandbox-protected. Stop any server you start. "
        "Your wall-clock task allocation includes all analysis and tests. End with concise actual "
        "changes/checks/limitations; do not claim unexecuted results."
    )
    if previous:
        text += "\nPrior external acceptance summary: " + json.dumps(previous)
    if arm == "treatment":
        text += (
            "\nUse the explicitly supplied anaxigraph MCP service before substantial edits: scan, "
            "prepare_change, focused inspect and assess_plan, then scan/validate_change afterwards. "
            "Act on useful design guidance and record what you accepted or rejected and why. "
            "Do not debug or change AnaxiGraph itself. If its tool fails, record the failure and "
            "continue the product task within the same budget using normal source inspection."
        )
        if index in (2, 4):
            text += (
                " At this milestone, run full mapping, Patterns and Fresh Eyes workflows through "
                "that MCP service; use the saved results to assess better options, retaining "
                "Fresh Eyes source isolation. Keep all this within your remaining task budget; "
                "report incomplete workflows honestly rather than claiming they completed."
            )
    return text


def commit_and_push(root: Path, index: int) -> dict[str, Any]:
    result = {"committed": False, "pushed": False}
    try:
        if git(root, "status", "--porcelain"):
            git(root, "add", "-A")
            git(root, "commit", "-m", f"experiment: record Snow Rankings task {index}")
            result["committed"] = True
        result["revision"] = git(root, "rev-parse", "HEAD")
        git(root, "push", "origin", "main")
        result["pushed"] = True
    except (OSError, subprocess.SubprocessError):
        result["error"] = "Coordinator Git operation failed; local work preserved"
    return result


def verify(root: Path, port: int, milestone: int, directory: Path) -> dict[str, Any]:
    path = root / "run.json"
    if not path.is_file() or path.stat().st_size > 16_000:
        return {"status": "failed", "passed": 0, "total": 1, "reason": "Missing bounded run.json"}
    value = json.loads(path.read_text())
    args = value.get("command", value.get("argv")) if isinstance(value, dict) else value
    if not isinstance(args, list) or not args or any(not isinstance(x, str) or not x for x in args):
        return {
            "status": "failed",
            "passed": 0,
            "total": 1,
            "reason": "run.json command must be argv",
        }
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return {
                "status": "failed",
                "passed": 0,
                "total": 1,
                "reason": "Assigned port already occupied; no process stopped",
            }
    args = [x.replace("{port}", str(port)) for x in args]
    process = subprocess.Popen(
        args,
        cwd=root,
        env=environment(directory),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    children = {}
    try:
        until = time.monotonic() + 45
        while time.monotonic() < until and process.poll() is None:
            children.update(descendants(process.pid))
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.25)
        result = acceptance.evaluate(f"http://127.0.0.1:{port}", milestone)
        atomic(directory / f"acceptance-{milestone}.json", result)
        return {key: result[key] for key in ("status", "passed", "total")}
    finally:
        stop_owned(process, children)


def arm(arm_name: str, config: argparse.Namespace) -> dict[str, Any]:
    root = config.treatment if arm_name == "treatment" else config.control
    port = 18780 if arm_name == "treatment" else 18781
    directory = config.state / arm_name
    directory.mkdir(parents=True, exist_ok=True)
    lock = (directory / "coordinator.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    progress = {
        "arm": arm_name,
        "root": str(root),
        "model": "gpt-6.1-sol",
        "reasoning_effort": "xhigh",
        "coordinator_pid": os.getpid(),
        "started_at": now(),
        "status": "starting",
        "tasks": [],
    }

    def save():
        atomic(directory / "progress.json", progress)

    previous = None
    try:
        for index in range(1, config.task_limit + 1):
            stage(root, index)
            task = TASKS[index - 1]
            revision = git(root, "rev-parse", "HEAD")
            started = time.monotonic()
            progress.update({"task": task, "task_index": index, "task_started_at": now()})
            save()
            trial.record(
                config.state / "trial.sqlite",
                arm_name,
                task,
                "task_started",
                {
                    "task": task,
                    "starting_revision": revision,
                    "model": "gpt-6.1-sol",
                    "reasoning_effort": "xhigh",
                },
            )
            result = coding(
                root,
                arm_name,
                directory,
                config.runtime,
                prompt_for(arm_name, index, port, directory, previous),
                started + config.minutes * 60,
                progress,
                save,
            )
            elapsed = time.monotonic() - started
            trial.record(
                config.state / "trial.sqlite",
                arm_name,
                task,
                "coding_call",
                {
                    key: result.get(key)
                    for key in (
                        "session_id",
                        "input_tokens",
                        "output_tokens",
                        "cached_input_tokens",
                        "cost_usd",
                        "usage_basis",
                    )
                }
                | {
                    "model": "gpt-6.1-sol",
                    "reasoning_effort": "xhigh",
                    "status": result["status"],
                    "elapsed_seconds": elapsed,
                },
            )
            progress["phase"] = "external_acceptance"
            save()
            try:
                previous = verify(root, port, index, directory)
            except (ValueError, OSError, subprocess.SubprocessError):
                previous = {
                    "status": "failed",
                    "passed": 0,
                    "total": 1,
                    "reason": "App launch or acceptance failed",
                }
            trial.record(
                config.state / "trial.sqlite",
                arm_name,
                task,
                "acceptance",
                {key: previous[key] for key in ("status", "passed", "total")},
            )
            publication = commit_and_push(root, index)
            outcome = {
                "task": task,
                "coding": result,
                "acceptance": previous,
                "elapsed_seconds": round(elapsed, 2),
                "publication": publication,
            }
            progress["tasks"].append(outcome)
            trial.record(
                config.state / "trial.sqlite",
                arm_name,
                task,
                "task_outcome",
                {
                    "starting_revision": revision,
                    "candidate_revision": publication.get("revision"),
                    "status": "completed"
                    if result["status"] == "completed" and previous["status"] == "passed"
                    else "incomplete",
                    "elapsed_seconds": elapsed,
                    "notes": "See bounded progress and shared acceptance record.",
                },
            )
            save()
        progress["status"] = (
            "completed"
            if all(
                x["coding"]["status"] == "completed" and x["acceptance"]["status"] == "passed"
                for x in progress["tasks"]
            )
            else "finished_with_failures"
        )
    except Exception as error:
        progress.update({"status": "failed", "error_type": type(error).__name__})
    finally:
        progress.update({"finished_at": now(), "phase": "finished"})
        save()
        lock.close()
    return progress


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--state", type=Path, default=DEFAULT_STATE)
    result.add_argument("--runtime", type=Path)
    result.add_argument("--treatment", type=Path, default=Path.home() / "repos/snow-rankings")
    result.add_argument("--control", type=Path, default=Path.home() / "repos/snow-rankings-control")
    result.add_argument("--minutes", type=float, default=20)
    result.add_argument("--task-limit", type=int, choices=range(1, 5), default=4)
    result.add_argument("--dry-run", action="store_true")
    return result


def main() -> int:
    config = parser().parse_args()
    config.state = config.state.resolve()
    config.runtime = (config.runtime or config.state / "runtime/bin/anaxigraph").resolve()
    if config.minutes <= 0 or config.minutes > 120:
        raise ValueError("Task minutes must be in (0,120]")
    if not shutil.which("codex"):
        raise ValueError("Codex CLI is unavailable")
    if not config.runtime.is_file() and not config.dry_run:
        raise ValueError("Frozen treatment runtime is unavailable")
    if config.dry_run:
        print(
            json.dumps(
                {
                    name: command(root, name, config.state / name, config.runtime)
                    for name, root in (("treatment", config.treatment), ("control", config.control))
                },
                indent=2,
            )
        )
        return 0
    config.state.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(arm, name, config): name for name in ("treatment", "control")}
        results = {jobs[job]: job.result() for job in concurrent.futures.as_completed(jobs)}
    atomic(config.state / "summary.json", {"finished_at": now(), "arms": results})
    comparison = trial.compare(config.state / "trial.sqlite", HERE / "manifest.json")
    atomic(config.state / "comparison.json", comparison)
    print(json.dumps({name: value["status"] for name, value in results.items()}))
    return int(any(value["status"] != "completed" for value in results.values()))


if __name__ == "__main__":
    raise SystemExit(main())
