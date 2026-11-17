"""Pytest lifecycle and source-bound receipts over the standalone file audit."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from . import anaxigraph_audit as audit
    from . import anaxigraph_children as children
else:
    import anaxigraph_audit as audit
    import anaxigraph_children as children

_SESSION = uuid.uuid4().hex
_WORKERS: list[str] = []
_FINISHED: set[str] = set()
FORMAT = audit.FORMAT


def _manifest() -> dict:
    try:
        with open(os.environ["ANAXIGRAPH_RECORDING"]) as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else {}
    except (KeyError, OSError, ValueError):
        return {}


def _digest(path: str) -> str | None:
    try:
        with open(path, "rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError:
        return None


def _same_source(run: dict) -> bool:
    if run.get("format") != "anaxigraph-test-run-v1" or run.get("root") != audit._ROOT:
        return False
    for name, fingerprint in run.get("files", {}).items():
        path = os.path.join(audit._ROOT, name)
        if os.path.realpath(path) != path or not path.startswith(audit._ROOT + os.sep):
            return False
        if _digest(path) != fingerprint:
            return False
    return bool(run.get("files"))


_RECORDING = _manifest()
_UNCHANGED = _same_source(_RECORDING)


audit._AUDITING = True


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: Any) -> Any:
    audit._TEST[0] = audit._relative(str(getattr(item, "path", None) or item.fspath))
    yield
    audit._TEST[0] = None


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Any) -> None:
    node.workerinput["anaxigraph_session"] = _SESSION
    _WORKERS.append(node.workerinput["workerid"])


def pytest_configure(config: Any) -> None:
    global _SESSION
    _SESSION = getattr(config, "workerinput", {}).get("anaxigraph_session", _SESSION)
    if _UNCHANGED and os.environ.get("ANAXIGRAPH_TRACE_CHILDREN") == "1":
        children.install(_SESSION, _RECORDING["run_id"], not hasattr(config, "workerinput"))


def pytest_runtest_logreport(report: Any) -> None:
    if report.when == "teardown":
        _FINISHED.add(report.nodeid)


def _completion(session: Any, exitstatus: int, worker: str | None) -> bool:
    return bool(
        exitstatus in (0, 1)
        and _UNCHANGED
        and _same_source(_RECORDING)
        and session.testscollected > 0
        and (worker or len(_FINISHED) == session.testscollected)
    )


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_sessionfinish(session: Any, exitstatus: int) -> Any:
    yield  # JUnit and coverage finish before their fingerprints are captured.
    for identity, process in children.processes.items():
        if process.poll() is None:
            audit._SEEN["started"].setdefault("", set()).update(children.expected.pop(identity))
    observed_until = time.monotonic()
    audit._AUDITING = False
    worker = getattr(session.config, "workerinput", {}).get("workerid")
    report = {
        "format": FORMAT,
        "root": audit._ROOT,
        "run_id": _RECORDING.get("run_id"),
        "session_id": _SESSION,
        "workers": _WORKERS,
        "children": dict(children.expected),
        "observed_until": observed_until,
        "worker": worker,
        "collected_tests": session.testscollected,
        "finished_tests": len(_FINISHED),
        "environment": {"python": sys.version, "executable": sys.executable},
        "complete": _completion(session, exitstatus, worker),
        **audit._observations(),
    }
    if not worker:
        plugin = session.config.pluginmanager.getplugin("_cov")
        cov = getattr(getattr(plugin, "cov_controller", None), "cov", None)
        report["recording"] = _RECORDING or None
        report["artifacts"] = {
            "coverage": _digest(cov.config.data_file) if cov else None,
            "junit": _digest(session.config.option.xmlpath)
            if session.config.option.xmlpath
            else None,
        }
    target = f"{audit._TARGET}.{worker}" if worker else audit._TARGET
    temporary = target + f".{os.getpid()}.tmp"
    try:
        with open(temporary, "w") as handle:
            json.dump(report, handle)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
