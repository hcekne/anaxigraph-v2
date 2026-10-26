"""Tool-free CLI model workers; source access is never delegated to a worker.

Packets already contain the permitted evidence. Each invocation starts in a fresh
temporary directory with no repository, tools, MCP servers or previous session.
"""

from __future__ import annotations

import functools
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

from anaxigraph.evidence import provider_usage

NO_TOOLS = (
    "shell_tool",
    "multi_agent",
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "skill_search",
    "view_image",
    "image_generation",
    "memories",
    "code_mode",
    "shell_snapshot",
    "hooks",
    "plugins",
    "tool_suggest",
)
OUTPUT_BYTES = 8 * 1024 * 1024
_OUTPUT_LOCK = threading.Lock()
_ACTIVE_OUTPUT: dict[int, int] = {}


def configured_model(provider: str) -> str:
    """Read only the model choice; never import user tools, hooks or MCP config."""
    if provider != "codex":
        return ""
    directory = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    try:
        config = tomllib.loads((directory / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    profile = config.get("profiles", {}).get(config.get("profile", ""), {})
    model = profile.get("model", config.get("model", ""))
    return model if isinstance(model, str) else ""


@functools.lru_cache(maxsize=4)
def _verified(provider: str) -> None:
    if provider == "codex":
        flags = [part for feature in NO_TOOLS for part in ("--disable", feature)]
        result = subprocess.run(
            ["codex", *flags, "features", "list"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        states = {
            parts[0]: parts[-1]
            for line in result.stdout.splitlines()
            if len(parts := line.split()) >= 3
        }
        if any(states.get(feature) != "false" for feature in NO_TOOLS):
            raise ValueError("Codex cannot enforce tool-free workers; upgrade the CLI")
    elif provider == "claude":
        result = subprocess.run(
            ["claude", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        required = (
            "--tools",
            "--restricted",
            "--strict-mcp-config",
            "--setting-sources",
            "--system-prompt",
            "--no-session-persistence",
            "--safe-mode",
        )
        if any(flag not in result.stdout for flag in required):
            raise ValueError("Claude cannot enforce tool-free workers; upgrade the CLI")
    else:
        raise ValueError("Model provider must be codex or claude")


def reasoning_effort(worker: dict[str, Any]) -> str | None:
    effort = worker.get("reasoning_effort")
    if effort is not None and (
        worker.get("provider", "codex") != "codex"
        or effort not in ("low", "medium", "high", "xhigh")
    ):
        raise ValueError("reasoning_effort is low, medium, high or xhigh for Codex workers only")
    return effort


def _command(
    provider: str, model: str, directory: Path, effort: str | None = None
) -> tuple[list[str], Path | None]:
    if provider == "codex":
        command = ["codex"]
        command.extend(part for feature in NO_TOOLS for part in ("--disable", feature))
        if effort is not None:
            command.extend(["--config", f"model_reasoning_effort={json.dumps(effort)}"])
        command.extend(
            [
                "--config",
                'web_search="disabled"',
                "--config",
                "project_doc_max_bytes=0",
                "--config",
                "skills.include_instructions=false",
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--color",
                "never",
                "--json",
                "--output-last-message",
                str(directory / "result.json"),
            ]
        )
        if model:
            command.extend(["--model", model])
        command.append("-")
        return command, directory / "result.json"
    command = [
        "claude",
        "--print",
        "--output-format",
        "json",
        "--tools",
        "",
        "--restricted",
        "--safe-mode",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--no-session-persistence",
        "--disable-slash-commands",
        "--system-prompt",
        "You are an independent reviewer. Use only the supplied packet. Return one JSON object.",
    ]
    if model:
        command.extend(["--model", model])
    return command, None


def _result(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    value = json.loads(text)
    # Claude's --output-format=json wraps its final text in a transport envelope.
    if (
        isinstance(value, dict)
        and "type" in value
        and ("result" in value or "structured_output" in value)
    ):
        if value.get("is_error"):
            raise ValueError("Claude reported an unsuccessful model call")
        value = value.get("structured_output", value.get("result"))
        if isinstance(value, str):
            return _result(value)
    if not isinstance(value, dict):
        raise ValueError("Model response must be one JSON object")
    return value


def _wait(process: subprocess.Popen, paths: list[Path], timeout: int | None) -> None:
    deadline = time.monotonic() + timeout if timeout is not None else None
    try:
        while True:
            used = sum(path.stat().st_size for path in paths if path.exists())
            with _OUTPUT_LOCK:
                _ACTIVE_OUTPUT[process.pid] = used
                total = sum(_ACTIVE_OUTPUT.values())
            if used > OUTPUT_BYTES or total > 32 * 1024 * 1024:
                raise ValueError("Model output exceeded the bounded worker response budget")
            if process.poll() is not None:
                break
            if deadline is not None and time.monotonic() > deadline:
                raise ValueError(f"Model call exceeded {timeout} seconds")
            time.sleep(0.1)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
        with _OUTPUT_LOCK:
            _ACTIVE_OUTPUT.pop(process.pid, None)


def execute(
    request: dict[str, Any],
    worker: dict[str, Any],
    *,
    timeout: int | None = 600,
    draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider, model = worker.get("provider", "codex"), worker.get("model", "")
    effort = reasoning_effort(worker)
    model = model or configured_model(provider)
    _verified(provider)
    # Deliver only the blind public envelope, never a server's scope/source metadata.
    allowed = ("request_id", "kind", "packet", "response_schema")
    packet = {key: request[key] for key in allowed if key in request}
    instruction = (
        "Complete this analysis using only its packet. Return a JSON object matching the "
        "response_schema example (it describes required fields, not literal answers). "
        "Do not use tools, inspect files, or invent evidence. Preserve every declared member. "
        "For Fresh Eyes also provide functional_summary and functional_requirements (at most "
        "eight behavior-only statements) for a separate product-level design review.\n"
    )
    if draft is not None:
        instruction += (
            "Review and improve the previous pass. Correct unjustified ratings and omitted "
            "requirements. Return the COMPLETE corrected result, not commentary.\n"
        )
        packet["previous_pass"] = draft
    environment = dict(os.environ)
    for key in ("CODEX_THREAD_ID", "CLAUDECODE", "CLAUDE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT"):
        environment.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="anaxigraph-worker-") as temporary:
        directory = Path(temporary)
        command, result_path = _command(provider, model, directory, effort)
        prompt_path, output_path = directory / "input.json", directory / "stdout.log"
        prompt_path.write_text(
            instruction + json.dumps(packet, ensure_ascii=False), encoding="utf-8"
        )
        with prompt_path.open("rb") as source, output_path.open("wb") as output:
            process = subprocess.Popen(
                command,
                stdin=source,
                stdout=output,
                stderr=subprocess.DEVNULL,
                cwd=directory,
                env=environment,
                start_new_session=True,
            )
            paths = [output_path] + ([result_path] if result_path is not None else [])
            _wait(process, paths, timeout)
        if process.returncode:
            # Do not publish CLI stderr: it can contain credentials or complete source prompts.
            raise ValueError(
                f"{provider} worker exited {process.returncode}; check CLI compatibility, authentication or provider availability"
            )
        chosen = result_path if result_path is not None else output_path
        if not chosen.exists() or chosen.stat().st_size > OUTPUT_BYTES:
            raise ValueError("Worker did not produce a bounded final response")
        result = _result(chosen.read_text(encoding="utf-8"))
        # Read transport metadata, never a usage claim generated inside the answer.
        result["_anaxigraph_usage"] = provider_usage(
            provider, output_path.read_text(encoding="utf-8")
        )
        return result
