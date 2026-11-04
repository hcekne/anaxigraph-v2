"""Hidden acceptance fixtures for AetherLoom: the candidate runs as a subprocess.

`DRIFT_CANDIDATE` is the candidate checkout; `DRIFT_STEP` is the step being evaluated. The
only era change is P1 (request 7): from then on new fields default to reflective boundaries,
so checks written for earlier requests compute their expectation for that boundary.
AetherLoom has no external services; expected numbers come from `loom_model.py`.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loom_model as lm  # noqa: E402

CANDIDATE = Path(os.environ.get("DRIFT_CANDIDATE", ".")).resolve()
STEP = int(os.environ.get("DRIFT_STEP", "20"))
DEFAULT_BOUNDARY = "reflective" if STEP >= 7 else "absorbing"
SCRATCH = Path(os.environ.get("TMPDIR") or tempfile.gettempdir())


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def candidate_python() -> str:
    return str(CANDIDATE / ".venv" / "bin" / "python")


class App:
    """One running candidate service with its own data directory."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="loom-app-", dir=SCRATCH))
        self.data = self.root / "data"
        self.proc: subprocess.Popen | None = None
        self.http: httpx.Client | None = None
        self.start()

    def env(self) -> dict:
        return {
            **os.environ,
            "AETHERLOOM_DATA_DIR": str(self.data),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def start(self) -> None:
        port = free_port()
        command = json.loads((CANDIDATE / "run.json").read_text())["command"]
        argv = [part.replace("{port}", str(port)) for part in command]
        self.log = open(self.root / "service.log", "ab")
        self.proc = subprocess.Popen(
            argv, cwd=CANDIDATE, env=self.env(), stdout=self.log, stderr=subprocess.STDOUT
        )
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60)
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
        if self.http:
            self.http.close()
        self.log.close()

    def restart(self) -> None:
        self.stop()
        self.start()

    def close(self) -> None:
        self.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    # HTTP -----------------------------------------------------------------------------------
    def get(self, path, **kw):
        return self.http.get(path, **kw)

    def post(self, path, **kw):
        return self.http.post(path, **kw)

    def put(self, path, **kw):
        return self.http.put(path, **kw)

    def delete(self, path, **kw):
        return self.http.delete(path, **kw)

    # Shared setup helpers -------------------------------------------------------------------
    def field(self, radius: int = 3, k: float = 0.1, **extra) -> str:
        ident = "f-" + uuid.uuid4().hex[:10]
        body = {"id": ident, "radius": radius, **extra}
        if "planes" not in extra:
            body["k"] = k
        response = self.post("/api/fields", json=body)
        assert response.status_code == 201, response.text
        return ident

    def set_cells(self, fid: str, values: dict, plane: str | None = None) -> None:
        cells = []
        for (q, r), value in values.items():
            row = {"q": q, "r": r, "value": value}
            if plane is not None:
                row["plane"] = plane
            cells.append(row)
        response = self.put(f"/api/fields/{fid}/cells", json={"cells": cells})
        assert response.status_code == 200, response.text

    def cells(self, fid: str) -> dict:
        response = self.get(f"/api/fields/{fid}/cells")
        assert response.status_code == 200, response.text
        return {(c["q"], c["r"]): c["value"] for c in response.json()["cells"]}

    def plane_cells(self, fid: str) -> dict:
        """(q, r) -> list of plane values."""
        response = self.get(f"/api/fields/{fid}/cells")
        assert response.status_code == 200, response.text
        return {(c["q"], c["r"]): c["values"] for c in response.json()["cells"]}

    def summary(self, fid: str) -> dict:
        response = self.get(f"/api/fields/{fid}")
        assert response.status_code == 200, response.text
        return response.json()

    def step(self, fid: str, **body) -> dict:
        response = self.post(f"/api/fields/{fid}/step", json=body)
        assert response.status_code == 200, response.text
        return response.json()

    def transducer(self, fid: str, tid: str, cells) -> dict:
        body = {"id": tid, "cells": [{"q": q, "r": r} for q, r in cells]}
        response = self.post(f"/api/fields/{fid}/transducers", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    def frame(self, fid: str, tid: str, deltas) -> httpx.Response:
        body = {"deltas": [{"q": q, "r": r, "delta": d} for (q, r), d in deltas]}
        return self.post(f"/api/fields/{fid}/transducers/{tid}/frames", json=body)

    def wire(self, fid: str, tid: str, data: bytes) -> dict:
        response = self.post(
            f"/api/fields/{fid}/transducers/{tid}/wire",
            content=data,
            headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def audit(self, fid: str) -> dict:
        response = self.get(f"/api/fields/{fid}/audit")
        assert response.status_code == 200, response.text
        return response.json()

    # Out-of-process control planes (request 19) ---------------------------------------------
    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [candidate_python(), "-m", "aetherloom", "cli", *args],
            cwd=CANDIDATE,
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def mcp(self, messages: list[dict]) -> list[dict]:
        payload = "".join(json.dumps(m) + "\n" for m in messages)
        result = subprocess.run(
            [candidate_python(), "-m", "aetherloom", "mcp"],
            cwd=CANDIDATE,
            env=self.env(),
            input=payload,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def app():
    instance = App()
    yield instance
    instance.close()


@pytest.fixture
def fresh_app():
    instance = App()
    yield instance
    instance.close()


def error_code(response: httpx.Response) -> str | None:
    try:
        return response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


def assert_error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status and error_code(response) == code, (
        status,
        code,
        response.status_code,
        response.text[:500],
    )


def near(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def assert_cells(actual: dict, expected: dict, tol: float | None = None) -> None:
    """Exact (bit-identical) comparison unless a tolerance is given."""
    assert actual.keys() == expected.keys()
    for cell, value in expected.items():
        got = actual[cell]
        if tol is None:
            assert got == value, (cell, got, value)
        else:
            assert near(got, value, tol), (cell, got, value)


def conserved(audit: dict) -> bool:
    return audit["ok"] is True and abs(audit["drift"]) <= 1e-12 * max(
        1.0, abs(audit["expected_total"])
    )


__all__ = ["lm", "App"]
