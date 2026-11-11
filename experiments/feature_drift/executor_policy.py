"""One qualified Codex policy for local Python concurrency with denied external egress.

The legacy restricted-network helper blocks AF_UNIX send, including asyncio's
self-pipe. A named workspace profile uses the deny-all proxy instead. The
preflight executes local code through `codex sandbox`; it never starts a model.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

try:
    from .process_guard import run
except ImportError:
    from process_guard import run

PROFILE = "drift-local"
POLICY = {
    "id": "workspace-local-concurrency-deny-egress-v1",
    "profile": PROFILE,
    "extends": ":workspace",
    "filesystem": {":root": "read"},
    "network": {
        "enabled": True,
        "domains": {},
        "allow_upstream_proxy": False,
        "allow_local_binding": False,
    },
    "network_proxy": True,
    "preflight": "resolved-profile-local-concurrency-denied-egress-and-reads-v2",
}


def policy(denied_roots=()) -> dict:
    return POLICY | {
        "filesystem": POLICY["filesystem"]
        | {str(Path(path).resolve()): "deny" for path in denied_roots}
    }


def settings(denied_roots=()) -> dict[str, str]:
    filesystem = ",".join(
        f"{json.dumps(path)}={json.dumps(access)}"
        for path, access in policy(denied_roots)["filesystem"].items()
    )
    return {
        "default_permissions": json.dumps(PROFILE),
        f"permissions.{PROFILE}": (
            '{extends=":workspace",filesystem={' + filesystem + "},"
            "network={enabled=true,domains={},allow_upstream_proxy=false,"
            "allow_local_binding=false}}"
        ),
        "features.network_proxy.enabled": "true",
    }


PREFLIGHT = """import asyncio, errno, json, os, socket, sys, threading, urllib.error, urllib.request
from pathlib import Path
public = Path.cwd() / "allowed-canary"
public.write_text("allowed")
assert public.read_text() == "allowed"
try:
    Path(sys.argv[1]).read_text()
except OSError as error:
    assert error.errno in (errno.EACCES, errno.EPERM, errno.ENOENT), repr(error)
else:
    raise AssertionError("Private benchmark canary was readable")
a, b = socket.socketpair()
try:
    assert a.send(b"local") == 5 and b.recv(5) == b"local"
finally:
    a.close(); b.close()
loop = asyncio.new_event_loop()
worker = threading.Thread(target=loop.run_forever, daemon=True)
worker.start()
try:
    future = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, result="awake"), loop)
    assert future.result(timeout=3) == "awake"
finally:
    loop.call_soon_threadsafe(loop.stop)
    worker.join(timeout=3)
    assert not worker.is_alive()
    loop.close()
assert os.environ.get("HTTP_PROXY") and os.environ.get("HTTPS_PROXY")
try:
    connection = socket.create_connection(("192.0.2.1", 80), timeout=2)
except OSError as error:
    assert error.errno in (errno.ENETUNREACH, errno.EACCES, errno.EPERM), repr(error)
else:
    connection.close()
    raise AssertionError("Direct external egress was allowed")
try:
    urllib.request.urlopen("http://anaxigraph-preflight.invalid/", timeout=3)
except urllib.error.HTTPError as error:
    assert error.code == 403, error.code
    denial = json.loads(error.read(4096))
    assert denial.get("status") == "blocked" and denial.get("decision") == "deny", denial
    assert denial.get("source") == "baseline_policy", denial
    assert denial.get("reason") == "not_allowed", denial
    assert denial.get("host") == "anaxigraph-preflight.invalid", denial
else:
    raise AssertionError("Deny-all proxy did not reject the external host")
print(json.dumps({"socketpair": True, "cross_thread_asyncio": True, "egress_denied": True,
                  "private_read_denied": True, "workspace_read_write": True}))
"""

_lock = threading.Lock()
_verified: dict[tuple, dict] = {}


def resolved_profile(executable: Path, directory: str, denied_roots=()) -> dict:
    """Resolve default_permissions without inference; sandbox's -P is mandatory."""
    argv = [str(executable), "--cd", directory]
    values = settings(denied_roots) | {
        "approval_policy": '"never"',
        "project_doc_max_bytes": "0",
        "mcp_servers": "{}",
        "model_provider": '"drift-no-inference"',
        "model_providers.drift-no-inference": (
            '{name="No inference",base_url="http://127.0.0.1:9",'
            'wire_api="responses",requires_openai_auth=false}'
        ),
    }
    for name, value in values.items():
        argv.extend(["--config", f"{name}={value}"])
    argv.extend(["debug", "prompt-input", "Render permissions only; do not start inference."])
    result = run(argv, Path(directory), 20)
    try:
        messages = json.loads(result.stdout)
        blocks = [
            content["text"]
            for message in messages
            if message.get("role") == "developer"
            for content in message.get("content", [])
            if content.get("text", "").startswith("<permissions instructions>")
        ]
        qualified = len(blocks) == 1 and all(
            marker in blocks[0]
            for marker in ("`sandbox_mode` is `workspace-write`", "Network access is enabled.")
        )
    except (ValueError, TypeError, AttributeError, KeyError):
        qualified = False
    if result.returncode or not qualified:
        raise ValueError(
            "Codex executor profile resolution failed; no paid session started: "
            + result.stderr[-1500:]
        )
    return {
        "selection": "default_permissions",
        "workspace_write": True,
        "local_network_syscalls": True,
        "resource_usage": result.resource_usage,
        "models_started": 0,
    }


def preflight(denied_roots=()) -> dict:
    """Fail before a paid session if this executable cannot uphold the named policy."""
    executable = shutil.which("codex")
    if executable is None:
        raise ValueError("Codex executor preflight failed: codex is not installed")
    path = Path(executable).resolve(strict=True)
    stat = path.stat()
    denied_roots = tuple(sorted(str(Path(item).resolve()) for item in denied_roots))
    key = (str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns, denied_roots)
    with _lock:
        if key in _verified:
            return _verified[key]
        with (
            tempfile.TemporaryDirectory(prefix="drift-executor-") as directory,
            tempfile.TemporaryDirectory(prefix="drift-private-") as private,
        ):
            resolution = resolved_profile(path, directory, denied_roots)
            canary = Path(private) / "canary"
            canary.write_text("synthetic-benchmark-canary")
            argv = [str(path), "sandbox", "-C", directory, "-P", PROFILE]
            for name, value in settings((*denied_roots, private)).items():
                argv.extend(["--config", f"{name}={value}"])
            argv.extend([str(Path(sys.executable).resolve()), "-c", PREFLIGHT, str(canary)])
            result = run(argv, Path(directory), 20)
        expected = {
            "socketpair": True,
            "cross_thread_asyncio": True,
            "egress_denied": True,
            "private_read_denied": True,
            "workspace_read_write": True,
        }
        try:
            observed = json.loads(result.stdout.strip())
        except ValueError:
            observed = None
        if result.returncode or observed != expected:
            raise ValueError(
                "Codex executor preflight failed; no paid session started: "
                + (result.stderr or result.stdout)[-2000:]
            )
        receipt = {
            "status": "passed",
            "policy": policy(denied_roots),
            "executable": str(path),
            "checks": observed,
            "profile_resolution": resolution,
            "resource_usage": result.resource_usage,
            "models_started": 0,
        }
        _verified[key] = receipt
        return receipt
