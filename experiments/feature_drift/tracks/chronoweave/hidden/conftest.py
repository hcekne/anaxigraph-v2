"""Hidden acceptance fixtures for ChronoWeave: fake outside services in-process, the candidate
as a subprocess launched from its `run.json`.

`DRIFT_CANDIDATE` is the candidate checkout; `DRIFT_STEP` is the step being evaluated. It
selects era-dependent expectations introduced by the inline fault probes: P1 (step 7) moves
tie-breaking from record order to BLAKE2b payload digests, P2 (step 14) quarantines poisoned
promotions, P3 (step 20) rejects causal cycles.
"""

from __future__ import annotations

import hashlib
import itertools
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
P1 = STEP >= 7  # digest tie-break
P2 = STEP >= 14  # poisoned promotions are quarantined
P3 = STEP >= 20  # causal cycles rejected


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def uid(prefix: str = "x") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256(value) -> str:
    data = value if isinstance(value, bytes) else canonical(value)
    return hashlib.sha256(data).hexdigest()


def payload_digest(record: dict) -> str:
    """Request 7: BLAKE2b-256 over the canonical payload of an assertion."""
    payload = {k: record[k] for k in ("id", "key", "value", "author", "node", "causal_parents")}
    return hashlib.blake2b(canonical(payload), digest_size=32).hexdigest()


def error_code(response: httpx.Response) -> str | None:
    try:
        return response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


def expect_error(response: httpx.Response, status: int, code: str) -> None:
    assert (response.status_code, error_code(response)) == (status, code), response.text[:500]


def subset(record: dict, expected: dict) -> bool:
    return all(k in record and record[k] == v for k, v in expected.items())


class App:
    """One running candidate service with its own data directory."""

    def __init__(self, hub_url: str) -> None:
        self.hub_url = hub_url
        self.root = Path(tempfile.mkdtemp(prefix="cw-app-"))
        self.data = self.root / "data"
        self.proc: subprocess.Popen | None = None
        self.now = 1_000_000
        self.start()

    @property
    def env(self) -> dict:
        return {
            **os.environ,
            "CHRONOWEAVE_EXTERNAL_URL": self.hub_url,
            "CHRONOWEAVE_DATA_DIR": str(self.data),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def start(self) -> None:
        port = free_port()
        command = json.loads((CANDIDATE / "run.json").read_text())["command"]
        argv = [part.replace("{port}", str(port)) for part in command]
        self.log = open(self.root / "service.log", "ab")
        self.proc = subprocess.Popen(
            argv, cwd=CANDIDATE, env=self.env, stdout=self.log, stderr=subprocess.STDOUT
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

    # HTTP --------------------------------------------------------------------------------
    def call(self, method, path, tenant=None, role=None, **kwargs) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        if tenant:
            headers["X-Tenant"] = tenant
        if role:
            headers["X-Role"] = role
        return self.http.request(method, path, headers=headers, **kwargs)

    def get(self, path, tenant=None, **kw):
        return self.call("GET", path, tenant, **kw)

    def post(self, path, tenant=None, **kw):
        return self.call("POST", path, tenant, **kw)

    def put(self, path, tenant=None, **kw):
        return self.call("PUT", path, tenant, **kw)

    def delete(self, path, tenant=None, **kw):
        return self.call("DELETE", path, tenant, **kw)

    # Ledger helpers ----------------------------------------------------------------------
    def t(self, gap: int = 10) -> int:
        """A fresh, strictly later transaction time (shared by every ledger of this app)."""
        self.now += gap
        return self.now

    def write(
        self,
        key,
        value,
        effective_at,
        *,
        id=None,
        parents=(),
        branch="main",
        author="alice",
        node="local",
        asserted_at=None,
        tenant=None,
        **extra,
    ):
        body = {
            "id": id or uid("a-"),
            "key": key,
            "value": value,
            "effective_at": effective_at,
            "asserted_at": asserted_at if asserted_at is not None else self.t(),
            "causal_parents": list(parents),
            "author": author,
            "node": node,
            **extra,
        }
        return self.post(f"/api/branches/{branch}/assertions", tenant, json=body)

    def add(self, key, value, effective_at, **kw) -> dict:
        response = self.write(key, value, effective_at, **kw)
        assert response.status_code == 201, response.text[:500]
        return response.json()

    def retract(self, ident, asserted_at=None, tenant=None, **extra) -> httpx.Response:
        body = {"asserted_at": asserted_at if asserted_at is not None else self.t(), **extra}
        return self.post(f"/api/branches/main/assertions/{ident}/retract", tenant, json=body)

    def record(self, ident, branch="main", tenant=None, **params) -> dict:
        response = self.get(f"/api/branches/{branch}/assertions/{ident}", tenant, params=params)
        assert response.status_code == 200, response.text[:500]
        return response.json()

    def state(self, branch="main", tenant=None, **params) -> dict:
        """{key: (value, assertion_id)} of a materialized state."""
        params = {k: v for k, v in params.items() if v is not None}
        response = self.get(f"/api/branches/{branch}/state", tenant, params=params)
        assert response.status_code == 200, response.text[:500]
        return {e["key"]: (e["value"], e["assertion_id"]) for e in response.json()["entries"]}

    def fork(self, name=None, frm=None, tenant=None) -> str:
        name = name or uid("b-")
        body = {"name": name}
        if frm is not None:
            body["from"] = frm
        response = self.post("/api/branches", tenant, json=body)
        assert response.status_code == 201, response.text[:500]
        return name

    def merge(self, branch, tenant=None, **body) -> httpx.Response:
        body.setdefault("asserted_at", self.t())
        return self.post(f"/api/branches/{branch}/merge", tenant, json=body)

    def tenant(self, **budgets) -> str:
        ident = uid("t-")
        body = {"id": ident}
        if budgets:
            body["budgets"] = budgets
        response = self.post("/api/tenants", json=body)
        assert response.status_code == 201, response.text[:500]
        return ident

    def seal(self, effective_from, effective_to, ident=None, tenant=None) -> dict:
        body = {
            "id": ident or uid("s-"),
            "effective_from": effective_from,
            "effective_to": effective_to,
            "asserted_at": self.t(),
        }
        response = self.post("/api/branches/main/seals", tenant, json=body)
        assert response.status_code == 201, response.text[:500]
        return response.json()


@pytest.fixture(scope="session")
def hub():
    server, state = hubmod.start()
    yield state
    server.shutdown()


@pytest.fixture(autouse=True)
def _fresh_hub(hub):
    hub.reset()
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


_BANDS = itertools.count(1)


def band() -> int:
    """A private effective-time band for one test (seals and compaction are interval-wide)."""
    return 10_000_000 + next(_BANDS) * 100_000


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()
