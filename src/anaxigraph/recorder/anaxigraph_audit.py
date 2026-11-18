"""Standard-library file audit shared by pytest and directly observed Python children."""

from __future__ import annotations

import hashlib
import os
import re
import site
import sys
import threading
from typing import Any

FORMAT = "anaxigraph-reads-v3"
_CACHE_SUFFIX = re.compile(r"\.[^.]+(?:\.opt-[^.]+)?\.pyc$")
_EVENTS = {
    "exec": "loaded",
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
_AUDITING = False
_SUPPRESS = threading.local()
_OWN_FRAMES = {__file__, os.path.join(os.path.dirname(__file__), "anaxigraph_children.py")}


def _opaque_pth(raw: bytes) -> bool:
    if len(raw) > 65_536:
        return True
    if not any(
        line.startswith((b"import ", b"import\t"))
        for line in raw.removeprefix(b"\xef\xbb\xbf").splitlines()
    ):
        return False
    # This exact coverage initializer only imports frozen os when both switches are absent.
    inactive_coverage = (
        hashlib.sha256(raw).hexdigest()
        == "ef2ed06d19867ec669c09a804060666a9cd5e383af0a9d11aa2de79b77d448e8"
    )
    active = getattr(sys.modules.get("coverage.control"), "process_startup", None)
    return not inactive_coverage or bool(
        os.getenv("COVERAGE_PROCESS_START")
        or os.getenv("COVERAGE_PROCESS_CONFIG")
        or getattr(active, "coverage", None)
    )


def unobserved_startup() -> bool:
    own = os.path.join(os.path.dirname(__file__), "bootstrap", "sitecustomize.py")
    if any(
        name in sys.modules and getattr(sys.modules[name], "__file__", None) != own
        for name in ("sitecustomize", "usercustomize")
    ):
        return True
    folders = site.getsitepackages()
    if site.ENABLE_USER_SITE:
        folders.append(site.getusersitepackages())
    try:
        for folder in folders:
            if not os.path.isdir(folder):
                continue
            for name in os.listdir(folder):
                if name.endswith(".pth"):
                    with open(os.path.join(folder, name), "rb") as stream:
                        if _opaque_pth(stream.read(65_537)):
                            return True
    except OSError:
        return True
    return False


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
    """An importer-read hint; exec events also record the actual code filename."""
    folder, _, name = path.rpartition("/")
    if name.endswith(".pyc") and folder.endswith("__pycache__"):
        return folder.removesuffix("__pycache__") + _CACHE_SUFFIX.sub(".py", name)
    return path if name.endswith(".py") else None


def _readers(frame: Any) -> list[str]:
    """At import, @ identifies the module that keeps what it reads. Otherwise record the
    active test and project callers (+module), so cached reads reach their other tests too.
    Outside tests, @ marks runner work. Skip recorder frames and stop at pytest frames,
    keeping the launcher outside the caller chain.
    """
    callers: dict[str, None] = {}
    while frame is not None and _RUNNER not in frame.f_code.co_filename:
        name = frame.f_code.co_filename
        if (
            name not in _OWN_FRAMES
            and name.startswith(_ROOT + os.sep)
            and "site-packages" not in name
        ):
            if frame.f_code.co_name == "<module>":
                return ["@" + str(_relative(name))]
            callers[str(_relative(name))] = None
        frame = frame.f_back
    if _TEST[0] is None:
        return ["@" + next(iter(callers), "")]
    return [_TEST[0]] + ["+" + path for path in callers if path != _TEST[0]]


def _shown(frame: Any) -> bool:
    """Whether Python fetches source to show a line of it, in a traceback or a warning. The
    code it belongs to ran in the same test, so the read adds nothing to what executed."""
    names = set()
    while frame is not None and not frame.f_code.co_filename.startswith(_ROOT + os.sep):
        names.add(os.path.basename(frame.f_code.co_filename))
        frame = frame.f_back
    return "linecache.py" in names and not names.isdisjoint({"traceback.py", "warnings.py"})


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


def _named(kind: str, event: str, args: tuple, frame: Any) -> str | None:
    """The path an event is about, when it counts: "" for a process that may read anything."""
    if kind == "started":
        return None if _elsewhere(event, args) else ""
    if kind == "opened" and isinstance(args[1], str) and args[1][:1] in {"w", "x"}:
        return None  # Created or replaced, not read.
    path = _relative(args[0].co_filename if kind == "loaded" else args[0])
    shown = kind == "opened" and path is not None and path.endswith(".py") and _shown(frame)
    return None if shown else path


def _note(kind: str, event: str, args: tuple, frame: Any) -> None:
    path = _named(kind, event, args, frame)
    if path is None:
        return
    if kind in {"opened", "listed"} and _importing(frame):
        module = _module(path) if kind == "opened" else None
        if module is not None:
            _SEEN["loaded"].setdefault(module, set())
        if module is not None or kind == "listed":
            return  # A module being loaded, or a folder searched for one: code, not data.
    _SEEN[kind].setdefault(path, set()).update(() if kind == "loaded" else _readers(frame))


def _audit(event: str, args: tuple) -> None:
    kind = _EVENTS.get(event)
    if (
        _AUDITING
        and kind is not None
        and not (kind == "started" and getattr(_SUPPRESS, "active", False))
    ):
        try:
            _note(kind, event, args, sys._getframe().f_back)
        except Exception:  # noqa: BLE001 - never break the run that is being observed.
            # What could not be understood counts as a process: the test is always selected.
            _SEEN["started"].setdefault("", set()).add(_TEST[0] or "@")


sys.addaudithook(_audit)  # At import, so conftest files loaded before configuration are seen.


def _observations() -> dict:
    for module in list(sys.modules.values()):  # Also what was imported before this plugin.
        try:
            name = getattr(module, "__file__", None)
        except Exception:  # noqa: BLE001 - a lazy module may fail when asked.
            continue
        if isinstance(name, str) and name.endswith(".py") and _relative(name):
            _SEEN["loaded"].setdefault(str(_relative(name)), set())
    return {
        kind: {
            path: sorted(readers)
            for path, readers in list(seen.items())
            if os.path.lexists(os.path.join(_ROOT, path))  # Not a file a test made and removed.
        }
        for kind, seen in _SEEN.items()
    }
