"""Bound neutral verification subprocesses without changing coding-model deadlines."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TypedDict


class VerificationPolicy(TypedDict):
    id: str
    rss_limit_bytes: int
    output_limit_bytes: int
    sample_interval_seconds: float


RESOURCE_POLICY: VerificationPolicy = {
    "id": "neutral-verification-process-v1",
    "rss_limit_bytes": 2 * 1024**3,
    "output_limit_bytes": 64 * 1024**2,
    "sample_interval_seconds": 0.05,
}


class ProcessResult(subprocess.CompletedProcess[str]):
    def __init__(self, args, returncode, stdout, stderr, resource_usage):
        super().__init__(args, returncode, stdout, stderr)
        self.resource_usage = resource_usage


def _identity(pid: int) -> tuple[int, int, int] | None:
    """Linux start tick, process group and resident bytes; no candidate code is imported."""
    try:
        fields = (Path("/proc") / str(pid) / "stat").read_text().rpartition(")")[2].split()
        if fields[0] == "Z":
            return None
        return int(fields[19]), int(fields[2]), int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def _sample(root: int, tracked: dict[int, int]) -> int | None:
    pending, visited = [root, *tracked], set()
    total = 0
    while pending:
        pid = pending.pop()
        if pid in visited:
            continue
        visited.add(pid)
        identity = _identity(pid)
        if identity is None or (pid in tracked and identity[0] != tracked[pid]):
            continue
        tracked[pid] = identity[0]
        total += identity[2]
        try:
            for task in (Path("/proc") / str(pid) / "task").iterdir():
                try:
                    pending.extend(map(int, (task / "children").read_text().split()))
                except (OSError, ValueError):
                    continue
        except OSError:
            continue
    return total if total else None


def _signal_group(group: int, tracked: dict[int, int], selected: signal.Signals) -> list[int]:
    alive = {
        pid: identity
        for pid, start in tracked.items()
        if (identity := _identity(pid)) is not None and identity[0] == start
    }
    signaled: list[int] = []
    if any(identity[1] == group for identity in alive.values()):
        try:
            os.killpg(group, selected)
            signaled.extend(pid for pid, identity in alive.items() if identity[1] == group)
        except ProcessLookupError:
            pass
    for pid, identity in alive.items():
        if identity[1] == group:
            continue
        try:
            os.kill(pid, selected)
            signaled.append(pid)
        except ProcessLookupError:
            pass
    return signaled


def _cleanup(process: subprocess.Popen, tracked: dict[int, int]) -> list[int]:
    _sample(process.pid, tracked)
    signaled = _signal_group(process.pid, tracked, signal.SIGTERM)
    if signaled:
        time.sleep(0.1)
        _sample(process.pid, tracked)
        signaled.extend(_signal_group(process.pid, tracked, signal.SIGKILL))
    # Even a process that exited before the first sample owns a new process group.
    # Normal descendants remain in that group and must not outlive verification.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    return sorted(set(signaled))


def run(
    argv: list[str],
    cwd: Path,
    timeout: float,
    env: dict | None = None,
    stdin: str = "",
    *,
    rss_limit_bytes: int = RESOURCE_POLICY["rss_limit_bytes"],
    output_limit_bytes: int = RESOURCE_POLICY["output_limit_bytes"],
    sample_interval_seconds: float = RESOURCE_POLICY["sample_interval_seconds"],
) -> ProcessResult:
    started = time.monotonic()
    deadline = started + timeout if timeout is not None else None
    peak, samples = 0, 0
    tracked: dict[int, int] = {}
    reason = "completed"
    with (
        tempfile.TemporaryFile() as source,
        tempfile.TemporaryFile() as output,
        tempfile.TemporaryFile() as errors,
    ):
        source.write(stdin.encode("utf-8"))
        source.seek(0)
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=source,
            stdout=output,
            stderr=errors,
            env={**os.environ, **(env or {})},
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                measured = _sample(process.pid, tracked)
                if measured is not None:
                    peak, samples = max(peak, measured), samples + 1
                size = os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size
                if measured is not None and measured > rss_limit_bytes:
                    reason = "rss_limit"
                elif size > output_limit_bytes:
                    reason = "output_limit"
                elif deadline is not None and time.monotonic() >= deadline:
                    reason = "timeout"
                if reason != "completed":
                    break
                try:
                    process.wait(timeout=sample_interval_seconds)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            signaled = _cleanup(process, tracked)
        stdout_bytes = os.fstat(output.fileno()).st_size
        stderr_bytes = os.fstat(errors.fileno()).st_size
        if stdout_bytes + stderr_bytes > output_limit_bytes and reason == "completed":
            reason = "output_limit"
        output.seek(0)
        raw = output.read(output_limit_bytes)
        errors.seek(0)
        stderr = errors.read(max(0, output_limit_bytes - len(raw))).decode("utf-8", "replace")
        if reason != "completed":
            diagnostic = "timeout" if reason == "timeout" else f"resource limit: {reason}"
            stderr = f"{stderr}\n{diagnostic}".lstrip("\n")
        policy = RESOURCE_POLICY | {
            "rss_limit_bytes": rss_limit_bytes,
            "output_limit_bytes": output_limit_bytes,
            "sample_interval_seconds": sample_interval_seconds,
        }
        return ProcessResult(
            argv,
            124 if reason == "timeout" else 137 if reason != "completed" else process.returncode,
            raw.decode("utf-8", "replace"),
            stderr,
            {
                "policy": policy,
                "termination_reason": reason,
                "peak_sampled_rss_bytes": peak if samples else None,
                "rss_sampling_supported": Path("/proc/self/stat").is_file(),
                "rss_samples": samples,
                "wall_timeout_seconds": timeout,
                "elapsed_seconds": round(time.monotonic() - started, 4),
                "stdout_bytes": stdout_bytes,
                "stderr_bytes": stderr_bytes,
                "output_truncated": stdout_bytes + stderr_bytes > output_limit_bytes,
                "cleanup_signaled_pids": signaled,
            },
        )
