"""Direct Python children: bounded receipts, with opaque work left conservative."""

from __future__ import annotations

import atexit
import fcntl
import json
import os
import subprocess
import sys
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import anaxigraph_audit as audit
else:
    import anaxigraph_audit as audit

expected: dict[str, list[str]] = {}
processes: dict[str, subprocess.Popen] = {}
_LIMIT = 10_000_000
_CONTEXT = ("ANAXIGRAPH_RECORDING", "ANAXIGRAPH_READS", "ANAXIGRAPH_ROOT")


def install(session: str, run_id: str, controller: bool) -> None:
    folder = os.path.dirname(__file__)
    prefix = os.path.join(folder, "bootstrap") + os.pathsep + folder
    if controller:
        try:
            fd = os.open(
                audit._TARGET + ".children",
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
            )
            os.close(fd)
        except OSError:
            return  # Read-only recording storage leaves every start opaque.

    class ObservedPopen(subprocess.Popen):
        def __init__(self, args, *rest, **options):
            readers = audit._readers(sys._getframe(1))
            context = _context(args, rest, options, session, run_id, readers)
            if context:
                expected[context["id"]] = readers
                supplied = options.get("env")
                environment = dict(os.environ if supplied is None else supplied)
                environment.update(
                    ANAXIGRAPH_ROOT=audit._ROOT, ANAXIGRAPH_CHILD=json.dumps(context)
                )
                original_path = os.fsdecode(environment.get("PYTHONPATH", ""))
                environment["PYTHONPATH"] = prefix + (
                    os.pathsep + original_path if original_path else ""
                )
                options["env"] = environment
            audit._SUPPRESS.active = bool(context)
            try:
                super().__init__(args, *rest, **options)
                if context:
                    processes[context["id"]] = self
            finally:
                audit._SUPPRESS.active = False

    subprocess.Popen = ObservedPopen  # type: ignore[misc]


def _context(args, rest, options, session, run_id, readers):
    env = options.get("env")
    if env is not None and any(env.get(key) != os.environ.get(key) for key in _CONTEXT):
        return None  # Preserve clean environments and independently configured recordings.
    if rest or options.get("shell") or options.get("executable") or audit._TEST[0] not in readers:
        return None
    if (
        not isinstance(args, (list, tuple))
        or not args
        or os.path.realpath(args[0]) != os.path.realpath(sys.executable)
    ):
        return None
    return {
        "id": uuid.uuid4().hex,
        "run_id": run_id,
        "session_id": session,
        "root": audit._ROOT,
        "log": audit._TARGET + ".children",
    }


def record_child(context: dict) -> None:
    if audit.unobserved_startup():
        return  # Startup code ran before this audit; keep the initiating tests opaque.
    audit._AUDITING = True

    def finish():
        observations = audit._observations()
        audit._AUDITING = False
        row = context | {
            "format": audit.FORMAT,
            "finished_at": time.monotonic(),
            "observations": {kind: list(paths) for kind, paths in observations.items()},
        }
        raw = (json.dumps(row, separators=(",", ":")) + "\n").encode()
        try:
            fd = os.open(
                context["log"], os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "ab", buffering=0) as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                if os.fstat(fd).st_size + len(raw) <= _LIMIT:
                    stream.write(raw)
        except OSError:
            pass  # A missing receipt retains the parent's opaque fallback.

    atexit.register(finish)
