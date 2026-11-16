"""Pytest plugin: which test read which file, listed which folder or started a process.

Coverage shows the code a test ran. It cannot show a manifest read as data, a folder scanned
for plug-ins or what a child process loaded, and a test can fail over any of them. Nor does
it say which files it was not asked to measure, so the modules the run loaded are kept too.
This records them with the standard library alone, so it runs in a project's own test
environment without AnaxiGraph installed there:

    PYTHONPATH="$(anaxigraph tests --recorder)" python -m pytest -p anaxigraph_reads ...

The result is written to $ANAXIGRAPH_READS (default .anaxigraph-reads.json) when the run
ends; under pytest-xdist every worker writes its own file beside it. The checkout is the
nearest folder upwards with a .git entry; set $ANAXIGRAPH_ROOT where there is none.
"""

from __future__ import annotations

import json
import os
import posixpath
import sys
from typing import Any

import pytest

FORMAT = "anaxigraph-reads-v1"
_EVENTS = {
    "open": "opened",
    "os.listdir": "listed",
    "os.scandir": "listed",
    "subprocess.Popen": "started",
    "os.system": "started",
    "os.posix_spawn": "started",
    "os.exec": "started",
    "os.fork": "started",
}


def _checkout(folder: str) -> str:
    """The nearest folder upwards that holds a .git entry, else the folder itself."""
    current = folder
    while not os.path.exists(os.path.join(current, ".git")):
        parent = os.path.dirname(current)
        if parent == current:
            return folder
        current = parent
    return current


_ROOT = os.path.abspath(os.environ.get("ANAXIGRAPH_ROOT") or _checkout(os.getcwd()))
_TARGET = os.path.abspath(os.environ.get("ANAXIGRAPH_READS", ".anaxigraph-reads.json"))
_SEEN: dict[str, dict[str, set[str]]] = {"opened": {}, "listed": {}, "started": {}, "loaded": {}}
_TEST: list[str | None] = [None]
_IMPORTS, _RUNNER = "<frozen importlib", os.sep + "_pytest" + os.sep
# Git options that take their value as the next word, and commands that read no tracked file.
_VALUED = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
_REFS = {"rev-parse", "symbolic-ref", "update-ref", "for-each-ref", "show-ref"}


def _relative(path: Any) -> str | None:
    """A path inside the checkout, relative to it with forward slashes."""
    if isinstance(path, int):  # A file descriptor names nothing.
        return None
    absolute = os.path.abspath(os.fsdecode(path))
    if absolute == _ROOT:
        return ""
    if not absolute.startswith(_ROOT + os.sep):
        return None
    return absolute[len(_ROOT) + 1 :].replace(os.sep, "/")


def _hooked(frame: Any) -> bool:
    """Whether pytest reads as an import hook, as it does for test files: called by the
    import system."""
    while frame is not None and _RUNNER in frame.f_code.co_filename:
        frame = frame.f_back
    return frame is not None and frame.f_code.co_filename.startswith(_IMPORTS)


def _importing(frame: Any) -> bool:
    """Whether the import system does the reading, before any code of the checkout."""
    while frame is not None:
        name = frame.f_code.co_filename
        if name.startswith(_IMPORTS):
            return True
        if _RUNNER in name:
            return _hooked(frame)
        if name.startswith(_ROOT + os.sep) and "site-packages" not in name:
            return False
        frame = frame.f_back
    return False


def _module(path: str) -> str | None:
    """The source file the import system loads when it reads a path: the path itself, or
    the source of cached bytecode."""
    folder, _, name = path.rpartition("/")
    if name.endswith(".pyc") and folder.endswith("__pycache__"):
        return posixpath.join(posixpath.dirname(folder), name.partition(".")[0] + ".py")
    return path if name.endswith(".py") else None


def _readers(frame: Any) -> list[str]:
    """Who depends on what is being read.

    "@module" while a module of the checkout is being imported: it keeps what it read for
    every later user. Otherwise the running test and "+file", the checkout's innermost file
    on the stack: code that reads once and caches serves other tests without reading again.
    Outside any test "@file", or "@" alone when no file of the checkout is involved.

    The stack is read from the inside out and only as far as pytest's own frames: whatever
    lies below them started the run and reads nothing.
    """
    first = None
    while frame is not None and _RUNNER not in frame.f_code.co_filename:
        name = frame.f_code.co_filename
        if name.startswith(_ROOT + os.sep) and "site-packages" not in name:
            if frame.f_code.co_name == "<module>":
                return ["@" + str(_relative(name))]
            first = first or name
        frame = frame.f_back
    inner = str(_relative(first)) if first else ""
    if _TEST[0] is None:
        return ["@" + inner]
    return [_TEST[0], "+" + inner] if inner not in ("", _TEST[0]) else [_TEST[0]]


def _command(words: list[str], folder: str) -> tuple[str | None, str, list[str]]:
    """A git command's name, the folder it works in after its own options, and the absolute
    paths it is given, also as the value of an option."""
    command, options = None, iter(words[1:])
    for word in options:
        if word in _VALUED:
            value = next(options, "")
            folder = os.path.join(folder, value) if word == "-C" else folder
        elif not word.startswith("-"):
            command = word
            break
    given = [word.partition("=")[2] if word.startswith("--") else word for word in words[1:]]
    return command, folder, [word for word in given if os.path.isabs(word)]


def _elsewhere(event: str, args: tuple) -> bool:
    """A git command that cannot read the checkout's files; any other process might.

    Git works on the repository it is started in unless told otherwise. It reads no file of
    the checkout when it is given no command, when it only asks about the names of commits,
    when it creates or copies a repository somewhere else, or when the folder it works in
    and every path it is given lie outside.
    """
    if event not in {"subprocess.Popen", "os.posix_spawn"} or isinstance(args[1], (str, bytes)):
        return False  # A shell command line, a fork or an exec: assume it reads the checkout.
    words = [os.fsdecode(word) for word in args[1]]
    if os.path.basename(os.fsdecode(args[0])) != "git":
        return False
    folder = os.fsdecode(args[2]) if event == "subprocess.Popen" and args[2] else os.getcwd()
    command, folder, given = _command(words, folder)
    if any(_relative(word) is not None for word in given):
        return False
    if command is None or (command in _REFS and ":" not in "".join(words)):
        return True
    return (command in {"init", "clone"} and words[-1] in given) or _relative(folder) is None


def _note(kind: str, event: str, args: tuple, frame: Any) -> None:
    if kind == "started":
        path: str | None = None if _elsewhere(event, args) else ""
    elif kind == "opened" and isinstance(args[1], str) and args[1][:1] in {"w", "x"}:
        path = None  # Created or replaced, not read.
    else:
        path = _relative(args[0])
    if path is None:
        return
    if kind != "started" and _importing(frame):
        module = _module(path) if kind == "opened" else None
        if module is not None:
            _SEEN["loaded"].setdefault(module, set())
        if module is not None or kind == "listed":
            return  # A module being loaded, or a folder searched for one: code, not data.
    _SEEN[kind].setdefault(path, set()).update(_readers(frame))


def _audit(event: str, args: tuple) -> None:
    kind = _EVENTS.get(event)
    if kind is not None:
        try:
            _note(kind, event, args, sys._getframe(1))
        except Exception:  # noqa: BLE001 - never break the run that is being observed.
            # What could not be understood counts as a process: the test is always selected.
            _SEEN["started"].setdefault("", set()).add(_TEST[0] or "@")


sys.addaudithook(_audit)  # At import, so conftest files loaded before configuration are seen.


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: Any) -> Any:
    _TEST[0] = _relative(str(getattr(item, "path", None) or item.fspath))
    yield
    _TEST[0] = None


def pytest_sessionfinish() -> None:
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    for module in list(sys.modules.values()):  # Also what was imported before this plugin.
        try:
            name = getattr(module, "__file__", None)
        except Exception:  # noqa: BLE001 - a lazy module may fail when asked.
            continue
        if isinstance(name, str) and name.endswith(".py") and _relative(name):
            _SEEN["loaded"].setdefault(str(_relative(name)), set())
    found = {
        kind: {
            path: sorted(readers)
            for path, readers in list(seen.items())
            if os.path.lexists(os.path.join(_ROOT, path))  # Not a file a test made and removed.
        }
        for kind, seen in _SEEN.items()
    }
    with open(f"{_TARGET}.{worker}" if worker else _TARGET, "w") as handle:
        json.dump({"format": FORMAT, "root": _ROOT, **found}, handle)
