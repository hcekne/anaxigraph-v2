"""Real child observations replace an opaque fallback only when attribution survives."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from anaxigraph import recorder
from anaxigraph.application import Application

FILES = {
    "app/__init__.py": "",
    "app/helper.py": "from pathlib import Path\ndef value():\n    return int(Path(__file__).with_name('value.txt').read_text()) + 0\n",
    "app/value.txt": "7",
    "app/other.py": "def other():\n    return 42\n",
    "app/worker.py": (
        "import os, subprocess, sys, time\nfrom pathlib import Path\n"
        "assert 'pytest' not in sys.modules and 'anaxigraph' not in sys.modules\n"
        "mode = sys.argv[1]\n"
        "if mode == 'crash': os._exit(0)\n"
        "if mode == 'capped':\n    import anaxigraph_children\n    anaxigraph_children._LIMIT = 1\n"
        "if mode == 'premature':\n    import atexit\n    atexit._run_exitfuncs()\n    Path(os.environ['READY_MARKER']).write_text('ready')\n"
        "if mode in {'late', 'premature'}:\n"
        "    deadline = time.monotonic() + 10\n"
        "    while not Path(os.environ['ANAXIGRAPH_READS']).exists() and time.monotonic() < deadline: time.sleep(0.01)\n"
        "if mode == 'descendant': subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
        "if mode == 'empty_env': assert not {'PARENT_ONLY_VALUE', 'ANAXIGRAPH_ROOT', 'ANAXIGRAPH_RECORDING'} & os.environ.keys()\n"
        "if mode in {'empty_env', 'bytes_env'}: assert os.getcwd() not in sys.path\n"
        "from helper import value\nprint(value())\n"
        "if mode in {'late', 'premature'}: Path(os.environ['DONE_MARKER']).write_text('done')\n"
    ),
    "app/client.py": (
        "import os, subprocess, sys, time\nfrom functools import lru_cache\nfrom pathlib import Path\n"
        "@lru_cache\ndef _value():\n"
        "    mode = os.environ['MODE']\n"
        "    flags = {'isolated': ['-I'], 'no_site': ['-S']}.get(mode, [])\n"
        "    args = [sys.executable, *flags, str(Path(__file__).with_name('worker.py')), mode]\n"
        "    kwargs = {'env': {}} if mode == 'empty_env' else {}\n"
        "    if mode == 'bytes_env': kwargs['env'] = dict(os.environ, PYTHONPATH=b'')\n"
        "    if mode == 'outside': kwargs['cwd'] = os.environ['OTHER_CWD']\n"
        "    if mode in {'killed', 'late', 'premature'}:\n"
        "        child = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)\n"
        "        if mode == 'killed': child.kill(); child.communicate()\n"
        "        if mode == 'premature':\n"
        "            deadline = time.monotonic() + 10\n"
        "            while not Path(os.environ['READY_MARKER']).exists() and time.monotonic() < deadline: time.sleep(0.01)\n"
        "        return 7\n"
        "    done = subprocess.run(args, capture_output=True, text=True, **kwargs)\n"
        "    if mode not in {'crash', 'isolated'}: assert done.returncode == 0, done.stderr\n"
        "    return int(done.stdout or '7')\n"
        "def value():\n    return _value()\n"
    ),
    "tests/test_first.py": "from app.client import value\ndef test_first():\n    assert value() == 7\n",
    "tests/test_second.py": "from app.client import value\ndef test_second():\n    assert value() == 7\n",
    "tests/test_other.py": "from app.other import other\ndef test_other():\n    assert other() == 42\n",
}
CLIENTS = ["tests/test_first.py", "tests/test_second.py"]


def make_recording(tmp_path_factory, mode):
    folder = tmp_path_factory.mktemp("child-record")
    root = folder / "source"
    for name, body in FILES.items():
        path = root / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(body)
    manifest = Application(root).start_recording("app")["manifest"]
    python_path = os.environ.get(
        "ANAXIGRAPH_RECORDER_TEST_PATH", str(Path(recorder.__file__).parent)
    )
    if mode == "customization":
        custom = folder / "custom"
        custom.mkdir()
        (custom / "sitecustomize.py").write_text(
            "import os\nos.environ['CUSTOM_STARTUP'] = str(os.getpid())\n"
        )
        python_path += os.pathsep + str(custom)
        worker = root / "app/worker.py"
        worker.write_text(
            worker.read_text() + "assert os.environ.get('CUSTOM_STARTUP') == str(os.getpid())\n"
        )
        Application(root).start_recording("app")
    environment = os.environ | {
        "PYTHONPATH": python_path,
        "ANAXIGRAPH_ROOT": str(root),
        "ANAXIGRAPH_RECORDING": manifest,
        "ANAXIGRAPH_READS": str(folder / "reads.json"),
        "ANAXIGRAPH_TRACE_CHILDREN": "1",
        "COVERAGE_FILE": str(folder / ".coverage"),
        "MODE": mode,
        "OTHER_CWD": str(folder),
        "DONE_MARKER": str(folder / "child.done"),
        "READY_MARKER": str(folder / "child.ready"),
        "PARENT_ONLY_VALUE": "fixture",
    }
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-q",
        "-p",
        "anaxigraph_reads",
        "--cov=app",
        "--cov-context=test",
        "--cov-report=",
        f"--junitxml={folder / 'junit.xml'}",
    ]
    if mode == "parallel":
        command.extend(["-n", "2"])
    done = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    if mode in {"late", "premature"}:
        deadline = time.monotonic() + 10
        log = folder / "reads.json.children"
        while (
            not (folder / "child.done").exists() or log.stat().st_size == 0
        ) and time.monotonic() < deadline:
            time.sleep(0.01)
        entry = json.loads(log.read_text().splitlines()[0])
        parent = json.loads((folder / "reads.json").read_text())
        assert (entry["finished_at"] > parent["observed_until"]) is (mode == "late")
        assert entry["id"] not in parent["children"]
        assert parent["started"]
    assert len(list(folder.glob("reads.json.children*"))) == 1
    return root, mode


@pytest.fixture(
    scope="module",
    params=[
        "normal",
        "empty_env",
        "bytes_env",
        "crash",
        "killed",
        "isolated",
        "no_site",
        "descendant",
        "customization",
        "late",
        "premature",
        "outside",
        "parallel",
        "capped",
    ],
)
def recorded(request, tmp_path_factory):
    return make_recording(tmp_path_factory, request.param)


@pytest.fixture(scope="module")
def ordinary(tmp_path_factory):
    return make_recording(tmp_path_factory, "normal")


def save(root, original, folder):
    return Application(root).record_tests(
        folder / ".coverage",
        "app",
        ["app", "tests"],
        recorded_root=str(original),
        junit=[folder / "junit.xml"],
        reads=folder / "reads.json",
    )


def test_child_code_and_data_reach_cached_callers_but_unrelated_work_stays_local(
    recorded, tmp_path
):
    original, mode = recorded
    root = Path(shutil.copytree(original, tmp_path / "clone"))
    assert save(root, original, original.parent)["provenance"]["status"] == "verified"
    app = Application(root)
    opaque = mode not in {"normal", "bytes_env", "outside", "parallel"}
    (root / "app/other.py").write_text("def other():\n    return 43\n")
    [suite] = app.tests_for_change()["suites"]
    if mode == "parallel":
        assert suite["status"] == "all"  # Unobserved controller startup remains conservative.
    else:
        assert suite["status"] == "selected"
        assert suite["test_files"] == sorted(["tests/test_other.py", *(CLIENTS if opaque else [])])
    (root / "app/other.py").write_text(FILES["app/other.py"])
    for name, body in (
        ("app/helper.py", FILES["app/helper.py"].replace("+ 0", "+ 1")),
        ("app/value.txt", "8"),
        ("app/worker.py", FILES["app/worker.py"] + "print('changed')\n"),
    ):
        (root / name).write_text(body)
        [suite] = app.tests_for_change()["suites"]
        if mode == "parallel" or (opaque and name == "app/value.txt" and mode != "descendant"):
            assert suite["status"] == "all"
        else:
            assert suite["status"] == "selected", suite
            assert suite["test_files"] == CLIENTS
        (root / name).write_text((original / name).read_text())


@pytest.mark.parametrize(
    "problem",
    ["missing", "truncated", "wrong_session", "late", "bad_shape", "bad_time", "foreign_root"],
)
def test_bad_child_receipts_keep_the_original_fallback(ordinary, tmp_path, problem):
    original, _ = ordinary
    root = Path(shutil.copytree(original, tmp_path / "clone"))
    reports = Path(
        shutil.copytree(original.parent, tmp_path / "reports", ignore=lambda *_: ["source"])
    )
    path = reports / "reads.json.children"
    row = json.loads(path.read_text().splitlines()[0])
    if problem == "missing":
        path.unlink()
    elif problem == "truncated":
        path.write_text('{"id":')
    else:
        if problem == "bad_shape":
            row["observations"]["loaded"] = "a filename is not a path list"
        elif problem == "bad_time":
            row["finished_at"] = True
        elif problem == "foreign_root":
            row["root"] = str(tmp_path)
        else:
            row.update(
                session_id="other" if problem == "wrong_session" else row["session_id"],
                finished_at=1e30 if problem == "late" else row["finished_at"],
            )
        path.write_text(json.dumps(row) + "\n")
    save(root, original, reports)
    (root / "app/other.py").write_text("def other():\n    return 43\n")
    [suite] = Application(root).tests_for_change()["suites"]
    assert suite["test_files"] == sorted(["tests/test_other.py", *CLIENTS])


def test_own_record_lookup_does_not_enumerate_other_worktrees(ordinary, tmp_path, monkeypatch):
    from anaxigraph.application import selection

    original, _ = ordinary
    root = Path(shutil.copytree(original, tmp_path / "clone"))
    save(root, original, original.parent)

    def forbidden(_):
        raise AssertionError("An existing local record does not need Git worktree discovery")

    monkeypatch.setattr(selection, "worktrees", forbidden)
    assert Application(root).tests_for_change()["suites"][0]["suite"] == "app"


def test_a_checkout_without_a_record_reuses_the_newest_recorded_worktree(
    ordinary, tmp_path, monkeypatch
):
    from anaxigraph.application import selection

    original, _ = ordinary
    peer = Path(shutil.copytree(original, tmp_path / "peer"))
    root = Path(shutil.copytree(original, tmp_path / "clone"))
    save(peer, original, original.parent)
    monkeypatch.setattr(selection, "worktrees", lambda _: [peer])
    [suite] = Application(root).tests_for_change()["suites"]
    assert suite["suite"] == "app" and suite["status"] == "nothing_to_run"
