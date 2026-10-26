"""Assess completed frozen commits with V2; preserve all original as-run data."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import socket
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import acceptance_v2
from run_pair import DEFAULT_STATE, descendants, stop_owned

HERE = Path(__file__).resolve().parent


def launch_args(command: list[str], repository: Path, snapshot: Path, port: int) -> list[str]:
    if not command or any(not isinstance(item, str) or not item for item in command):
        raise ValueError("Snapshot run.json must provide a command argv")
    values = list(command)
    if Path(values[0]).name == "uv":
        if len(values) < 3 or values[1] != "run":
            raise ValueError("Expected the published uv run startup command")
        values = values[2:]
        with_value = {"--cache-dir", "--project", "--directory", "--python", "--group", "--extra"}
        while values and values[0].startswith("-"):
            option = values.pop(0)
            if option in with_value:
                if not values:
                    raise ValueError("Incomplete uv run option")
                values.pop(0)
    if not values:
        raise ValueError("Missing app startup program")
    python = repository / ".venv" / "bin" / "python"
    if not python.is_file():
        raise ValueError("The existing arm dependency environment is unavailable")
    program = Path(values.pop(0)).name
    if program in {"python", "python3", "python3.11", "python3.12"}:
        result = [str(python), *values]
    elif program == "uvicorn":
        result = [str(python), "-m", "uvicorn", *values]
    else:
        raise ValueError(f"Unsupported frozen startup program: {program}")
    # Avoid importing current product source through an absolute project/app argument.
    return [
        item.replace(str(repository), str(snapshot)).replace("{port}", str(port))
        if item != str(python)
        else item
        for item in result
    ]


def snapshot(repository: Path, revision: str, destination: Path) -> str:
    resolved = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--verify", revision + "^{commit}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    archive = subprocess.run(
        ["git", "-C", str(repository), "archive", resolved],
        check=True,
        capture_output=True,
        timeout=45,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        for member in stream.getmembers():
            if (
                Path(member.name).is_absolute()
                or ".." in Path(member.name).parts
                or member.issym()
                or member.islnk()
            ):
                raise ValueError("Frozen archive contains a nonlocal input")
        stream.extractall(destination)
    return resolved


def assess(
    repository: Path, revision: str, milestone: int, port: int, temporary_root: Path
) -> dict[str, Any]:
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return {
                "status": "not_assessed",
                "reason": "Owned audit port is occupied; no process stopped",
            }
    with tempfile.TemporaryDirectory(prefix=f"m{milestone}-", dir=temporary_root) as temporary:
        root = Path(temporary)
        resolved = snapshot(repository, revision, root)
        startup = root / "run.json"
        if not startup.is_file() or startup.stat().st_size > 16_000:
            return {"status": "not_assessed", "reason": "Frozen commit has no bounded run.json"}
        value = json.loads(startup.read_text())
        argv = value.get("command", value.get("argv")) if isinstance(value, dict) else value
        if not isinstance(argv, list):
            raise ValueError("Frozen run.json must contain an argv list")
        command = launch_args(argv, repository, root, port)
        environment = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT")
            if key in os.environ
        }
        environment.update(
            {
                "PYTHONPATH": str(root),
                "PYTHONNOUSERSITE": "1",
                "TMPDIR": str(root),
                "XDG_CACHE_HOME": str(root / "cache"),
                "UV_CACHE_DIR": str(root / "uv-cache"),
            }
        )
        process = subprocess.Popen(
            command,
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        children = {}
        try:
            until, ready = time.monotonic() + 30, False
            while time.monotonic() < until and process.poll() is None:
                children.update(descendants(process.pid))
                try:
                    with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as response:
                        if response.status == 200:
                            ready = True
                            break
                except OSError:
                    time.sleep(0.1)
            if not ready:
                return {
                    "status": "not_assessed",
                    "reason": "Frozen app did not become ready in the existing dependency environment",
                    "source_commit": resolved,
                    "process_exit_code": process.poll(),
                }
            return acceptance_v2.evaluate(f"http://127.0.0.1:{port}", milestone) | {
                "source_commit": resolved,
                "source_isolation": "Exact Git archive with isolated cwd/PYTHONPATH; active worktree untouched",
                "dependency_environment": "Existing arm virtualenv; no installs; dependency state not recreated from frozen lockfile",
            }
        finally:
            stop_owned(process, children)


def adjudicate(state: Path, roots: dict[str, Path], through: int = 4) -> dict[str, Any]:
    state = state.resolve()
    if not state.is_relative_to(Path.home().resolve()):
        raise ValueError("Adjudication state and temporary data must stay under the user's home")
    temporary = state / "adjudication-tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    amendment = json.loads((HERE / "amendment-v2.json").read_text())
    checker_hash = hashlib.sha256((HERE / "acceptance_v2.py").read_bytes()).hexdigest()
    rows = []
    for arm in ("treatment", "control"):
        progress = state / arm / "progress.json"
        if not progress.is_file():
            continue
        for task in json.loads(progress.read_text()).get("tasks", []):
            milestone = int(task["task"].split("-", 1)[0])
            if milestone > through:
                continue
            revision = task.get("publication", {}).get("revision")
            if not revision:
                rows.append(
                    {
                        "arm": arm,
                        "task": task["task"],
                        "status": "not_assessed",
                        "reason": "No frozen candidate revision recorded",
                    }
                )
                continue
            original_path = state / arm / f"acceptance-{milestone}.json"
            original = (
                json.loads(original_path.read_text())
                if original_path.is_file()
                else task.get("acceptance")
            )
            try:
                result = assess(
                    roots[arm],
                    revision,
                    milestone,
                    18790 if arm == "treatment" else 18791,
                    temporary,
                )
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                result = {
                    "status": "not_assessed",
                    "reason": f"Adjudication environment error: {type(error).__name__}",
                }
            rows.append(
                {
                    "arm": arm,
                    "task": task["task"],
                    "source_commit": revision,
                    "original_as_run": original,
                    "adjudicated_v2": result,
                }
            )
    return {
        "schema_version": 1,
        "assessment_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checker_sha256": checker_hash,
        "amendment": amendment,
        "assessments": rows,
        "original_records_modified": False,
        "coding_agents_or_prompts_modified": False,
        "limitation": "Both agents received the original failed milestone1 summary; correction does not undo that influence.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--treatment", type=Path, default=Path.home() / "repos/snow-rankings")
    parser.add_argument("--control", type=Path, default=Path.home() / "repos/snow-rankings-control")
    parser.add_argument("--through", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = adjudicate(
        args.state, {"treatment": args.treatment, "control": args.control}, args.through
    )
    destination = args.output or args.state / "assessment-v2.json"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(
        json.dumps(
            {
                "output": str(destination),
                "assessments": [
                    {
                        "arm": row["arm"],
                        "task": row["task"],
                        "status": row.get("adjudicated_v2", row)["status"],
                        "passed": row.get("adjudicated_v2", {}).get("passed"),
                        "total": row.get("adjudicated_v2", {}).get("total"),
                    }
                    for row in result["assessments"]
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
