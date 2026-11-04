"""One bounded structural refresh per dashboard checkout, independent of its HTTP request."""

from __future__ import annotations

import threading
import time


def _scan_routes(server, application) -> None:
    lock = threading.Lock()
    state: dict = {"status": "idle", "phase": "No refresh running", "elapsed_seconds": 0}
    started = 0.0

    def progress(phase):
        with lock:
            state["phase"] = phase

    def run():
        try:
            result = application.scan(progress=progress)
            update = {"status": "completed", "phase": "Structural map ready", "result": result}
        except Exception as error:
            update = {"status": "failed", "phase": "Refresh failed", "error": str(error)}
        with lock:
            state.update(update, elapsed_seconds=round(time.monotonic() - started, 2))

    @server.get("/api/scan/status")
    def status() -> dict:
        with lock:
            elapsed = (
                time.monotonic() - started
                if state["status"] == "running"
                else state["elapsed_seconds"]
            )
            return state | {"elapsed_seconds": round(elapsed, 2)}

    @server.post("/api/scan/start", status_code=202)
    def start() -> dict:
        nonlocal started
        with lock:
            if state["status"] != "running":
                started = time.monotonic()
                state.clear()
                state.update(
                    status="running", phase="Starting structural refresh", elapsed_seconds=0
                )
                threading.Thread(target=run, daemon=True).start()
        return status()
