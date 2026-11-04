"""Deterministic fake outside services for the ChronoWeave track of the feature-drift benchmark.

One threaded HTTP server plays every outside service the requests mention. Candidates never
see this file; they only get the documentation inside each request. Hidden tests drive the
`Hub` object directly (same process):

- `/authority/v1/tenants/{tenant}/certify` (request 16): approves witnesses listed in
  `hub.witnesses[tenant]` (witness -> expires_at), denies others. Faults per tenant:
  `fail_next` (count), `fail_status`, `latency` (seconds).
- `/sinks/{name}` (request 13): records every delivery. Faults per sink name as above.

Run standalone with `python hub.py --port 8765`.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Hub:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.url = ""
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.calls: list[dict] = []
            self.faults: dict[str, dict] = {}
            self.witnesses: dict[str, dict[str, int]] = {}

    def fault(self, target: str, **values) -> None:
        with self.lock:
            self.faults.setdefault(target, {}).update(values)

    def approve(self, tenant: str, witness: str, expires_at: int) -> None:
        with self.lock:
            self.witnesses.setdefault(tenant, {})[witness] = expires_at

    def calls_to(self, service: str, part: str = "") -> list[dict]:
        with self.lock:
            return [c for c in self.calls if c["service"] == service and part in c["path"]]

    # ------------------------------------------------------------------------------------
    def _take_fault(self, target: str) -> tuple[float, int | None]:
        with self.lock:
            fault = self.faults.get(target, {})
            latency = float(fault.get("latency", 0))
            status = None
            if fault.get("fail_next", 0) > 0:
                fault["fail_next"] -= 1
                status = int(fault.get("fail_status", 500))
            return latency, status

    def handle(self, method: str, path: str, headers: dict, raw: bytes) -> tuple[int, bytes]:
        parts = [p for p in path.split("?")[0].split("/") if p]
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        if len(parts) == 2 and parts[0] == "sinks" and method == "POST":
            target = "sink:" + parts[1]
            service = "sinks"
        elif (
            len(parts) == 5
            and parts[:3] == ["authority", "v1", "tenants"]
            and parts[4] == "certify"
            and method == "POST"
        ):
            target = "authority:" + parts[3]
            service = "authority"
        else:
            return 404, b'{"error": "no such route"}'
        latency, status = self._take_fault(target)
        record = {
            "service": service,
            "target": target,
            "method": method,
            "path": path,
            "headers": {k.lower(): v for k, v in headers.items()},
            "raw": raw,
            "body": body,
            "at": time.monotonic(),
        }
        with self.lock:
            self.calls.append(record)
        if latency:
            time.sleep(latency)
        if status is not None:
            return status, b'{"error": "injected failure"}'
        if service == "sinks":
            return 200, b'{"ok": true}'
        tenant = parts[3]
        if not isinstance(body, dict):
            return 400, b'{"error": "bad body"}'
        with self.lock:
            expires = self.witnesses.get(tenant, {}).get(body.get("witness"))
        if expires is None:
            return 200, json.dumps({"approved": False}).encode()
        certificate = {
            "seal_id": body.get("seal_id"),
            "witness": body.get("witness"),
            "quorum": ["auth-1", "auth-2", "auth-3"],
            "expires_at": expires,
        }
        return 200, json.dumps({"approved": True, "certificate": certificate}).encode()


def start(port: int = 0) -> tuple[ThreadingHTTPServer, Hub]:
    hub = Hub()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _serve(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            status, payload = hub.handle(method, self.path, dict(self.headers), raw)
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except OSError:
                pass

        def do_GET(self):  # noqa: N802
            self._serve("GET")

        def do_POST(self):  # noqa: N802
            self._serve("POST")

        def log_message(self, *args):  # silence
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    hub.url = f"http://127.0.0.1:{server.server_address[1]}"
    return server, hub


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    srv, _ = start(args.port)
    print(f"hub on {srv.server_address}")
    threading.Event().wait()
