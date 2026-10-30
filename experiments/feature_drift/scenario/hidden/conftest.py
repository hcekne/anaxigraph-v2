"""Hidden acceptance fixtures: fake providers in-process, the candidate as a subprocess.

`DRIFT_CANDIDATE` is the candidate checkout; `DRIFT_STEP` is the step being evaluated,
which sets provider evolution (WeatherCo v1 retires at 11, MCP sessions start at 20).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hub as hubmod  # noqa: E402

CANDIDATE = Path(os.environ.get("DRIFT_CANDIDATE", ".")).resolve()
STEP = int(os.environ.get("DRIFT_STEP", "20"))

CREDS = {
    "weatherco": {"api_key": "wc-key-1"},
    "crm": {"token": "crm-token-a"},
    "docs": {"token": "docs-token"},
    "calendar": {"client_id": "cal-client-a", "client_secret": "cal-secret-a"},
    "ledger": {"api_key": "ledger-key-a", "account_id": "acc-100"},
    "tickets": {"token": "tickets-token-a"},
    "skycast": {"client_token": "sky-token-1"},
    "fxrates": {"app_id": "fx-app-1"},
    "lingo": {"token": "lingo-token"},
    "shiptrack": {"username": "ship-user", "password": "ship-pass"},
    "geo": {"api_key": "geo-key-1"},
    "notes": {"token": "notes-token"},
}
CREDS_B = {
    "crm": {"token": "crm-token-b"},
    "calendar": {"client_id": "cal-client-b", "client_secret": "cal-secret-b"},
    "ledger": {"api_key": "ledger-key-b", "account_id": "acc-200"},
    "tickets": {"token": "tickets-token-b"},
}
SECRETS = [
    "wc-key-1",
    "crm-token-a",
    "crm-token-b",
    "docs-token",
    "cal-secret-a",
    "cal-secret-b",
    "ledger-key-a",
    "ledger-key-b",
    "tickets-token-a",
    "tickets-token-b",
    "sky-token-1",
    "fx-app-1",
    "lingo-token",
    "ship-pass",
    "geo-key-1",
    "notes-token",
    "acc-100",
    "acc-200",
]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class App:
    """One running candidate service with its own data directory."""

    def __init__(self, hub_url: str) -> None:
        self.hub_url = hub_url
        self.root = Path(tempfile.mkdtemp(prefix="drift-app-"))
        self.data = self.root / "data"
        self.proc: subprocess.Popen | None = None
        self.start()

    def start(self) -> None:
        port = free_port()
        command = json.loads((CANDIDATE / "run.json").read_text())["command"]
        argv = [part.replace("{port}", str(port)) for part in command]
        env = {
            **os.environ,
            "SWITCHBOARD_EXTERNAL_URL": self.hub_url,
            "SWITCHBOARD_DATA_DIR": str(self.data),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.log = open(self.root / "service.log", "ab")
        self.proc = subprocess.Popen(
            argv, cwd=CANDIDATE, env=env, stdout=self.log, stderr=subprocess.STDOUT
        )
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                break
            try:
                if self.http.get("/api/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        tail = (self.root / "service.log").read_text(errors="replace")[-2000:]
        self.stop()
        pytest.fail(f"candidate service did not start:\n{tail}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.http.close()
        self.log.close()

    def restart(self) -> None:
        self.stop()
        self.start()

    def close(self) -> None:
        self.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def call(self, method: str, path: str, ws: str | None = None, **kwargs) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        if ws:
            headers["X-Workspace"] = ws
        return self.http.request(method, path, headers=headers, **kwargs)

    def get(self, path, ws=None, **kw):
        return self.call("GET", path, ws, **kw)

    def post(self, path, ws=None, **kw):
        return self.call("POST", path, ws, **kw)

    def put(self, path, ws=None, **kw):
        return self.call("PUT", path, ws, **kw)

    def delete(self, path, ws=None, **kw):
        return self.call("DELETE", path, ws, **kw)

    # Shared setup helpers -------------------------------------------------------------------
    def creds(self, *names: str, ws: str | None = None, variant: str = "a") -> None:
        for name in names:
            body = (CREDS_B if variant == "b" else CREDS).get(name, CREDS[name])
            response = self.put(f"/api/credentials/{name}", ws, json=body)
            assert response.status_code == 204, (name, response.status_code, response.text)

    def workspace(self) -> str:
        ident = "ws-" + uuid.uuid4().hex[:8]
        response = self.post("/api/workspaces", json={"id": ident, "name": ident.upper()})
        assert response.status_code == 201, response.text
        return ident

    def settings(self, ws: str, **values) -> dict:
        response = self.put(f"/api/workspaces/{ws}/settings", json=values)
        assert response.status_code == 200, response.text
        return response.json()

    def audit(self, ws: str | None = None, **params) -> list[dict]:
        params.setdefault("limit", 500)
        response = self.get("/api/audit", ws, params=params)
        assert response.status_code == 200, response.text
        return response.json()["entries"]


@pytest.fixture(scope="session")
def hub():
    server, state = hubmod.start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()


@pytest.fixture(autouse=True)
def _fresh_providers(hub):
    hub.reset()
    hub.step = STEP
    yield


@pytest.fixture(scope="module")
def app(hub):
    instance = App(hub.url)
    yield instance
    instance.close()


@pytest.fixture
def fresh_app(hub):
    instance = App(hub.url)
    yield instance
    instance.close()


def calls(hub, service: str, path_part: str = "") -> list[dict]:
    with hub.lock:
        return [c for c in hub.calls if c["service"] == service and path_part in c["path"]]


def fault(hub, service: str, **values) -> None:
    with hub.lock:
        hub.faults.setdefault(service, {}).update(values)


def error_code(response: httpx.Response) -> str | None:
    try:
        return response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


def close(a, b) -> bool:
    """Structural equality with float tolerance for unit conversions."""
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) < 0.011
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
    return a == b


def tool_calls(hub, server: str, tool: str | None = None) -> list[dict]:
    rows = []
    for call in calls(hub, server):
        body = call["body"]
        if isinstance(body, dict) and body.get("method") == "tools/call":
            if tool is None or body.get("params", {}).get("name") == tool:
                rows.append(body)
    return rows
