"""Real child observations replace an opaque fallback only when attribution survives."""

from __future__ import annotations

import json
import os
import py_compile
import shutil
import subprocess
import sys
import time
import venv
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
        "    args = [os.environ.get('CHILD_PYTHON', sys.executable), *flags, str(Path(__file__).with_name('worker.py')), mode]\n"
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
    if mode == "cross_cached":
        (root / "app/cache.py").write_text(
            "from functools import lru_cache\nfrom app.client import value as child_value\n"
            "@lru_cache\ndef _kept():\n    return child_value()\n"
            "def value():\n    return _kept()\n"
        )
        for name in CLIENTS:
            path = root / name
            path.write_text(path.read_text().replace("app.client", "app.cache"))
    cache_prefix = None
    if mode.startswith("cached_"):
        name = "plugin.config.py" if mode == "cached_dotted" else "plugin.py"
        source = root / "app" / name
        source.write_text("VALUE = 7\n")
        if mode == "cached_dotted":
            (root / "app/plugin.py").write_text("VALUE = 99\n")
        cache_prefix = str(folder / "bytecode") if mode == "cached_prefix" else None
        previous_prefix = sys.pycache_prefix
        try:
            sys.pycache_prefix = cache_prefix
            py_compile.compile(
                str(source), doraise=True, optimize=1 if mode == "cached_optimized" else 0
            )
        finally:
            sys.pycache_prefix = previous_prefix
        (root / "app/worker.py").write_text(
            "import importlib.util, os, sys\nfrom pathlib import Path\n"
            f"path = Path(__file__).with_name({name!r})\n"
            "spec = importlib.util.spec_from_file_location('dynamic_plugin', path)\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "original = spec.loader.get_data\n"
            "def cached(name):\n"
            "    if name == str(path) and os.environ.get('REQUIRE_WARM_CACHE'):\n"
            "        raise RuntimeError('Baseline must use compiled bytecode')\n"
            "    return original(name)\n"
            "spec.loader.get_data = cached\n"
            "spec.loader.exec_module(module)\n"
            "assert 'dynamic_plugin' not in sys.modules\nprint(module.VALUE)\n"
        )
    startup_python = None
    if mode in {"startup_hook", "parent_startup"}:
        prefix = folder / "startup-env"
        venv.EnvBuilder(symlinks=True, with_pip=False).create(prefix)
        startup_python = str(prefix / "bin/python")
        packages = (
            prefix / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
        (packages / "a1_coverage.pth").write_text(
            "import os; os.environ['STARTUP_VALUE'] = open(os.environ['STARTUP_INPUT']).read().strip()\n"
        )
        (root / "app/worker.py").write_text("import os\nprint(os.environ['STARTUP_VALUE'])\n")
        other = root / "tests/test_other.py"
        other.write_text(
            other.read_text()
            + "    from pathlib import Path\n"
            + "    assert Path('app/value.txt').read_text() == '7'\n"
        )
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
    if mode.startswith("cached_"):
        environment["REQUIRE_WARM_CACHE"] = "1"
    if cache_prefix:
        environment["PYTHONPYCACHEPREFIX"] = cache_prefix
    if mode == "cached_optimized":
        environment["PYTHONOPTIMIZE"] = "1"
    if startup_python:
        environment.update(CHILD_PYTHON=startup_python, STARTUP_INPUT=str(root / "app/value.txt"))
    if mode == "parent_startup":
        environment["PYTHONPATH"] += os.pathsep + str(Path(pytest.__file__).parent.parent)
    command = [
        startup_python if mode == "parent_startup" else sys.executable,
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
        "cross_cached",
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


@pytest.mark.parametrize(
    "mode", ["cached_plain", "cached_dotted", "cached_prefix", "cached_optimized"]
)
def test_warm_bytecode_dynamic_loads_retain_every_failing_consumer(
    tmp_path_factory, tmp_path, mode
):
    from anaxigraph.indexing.test_record import junit_cases

    original, _ = make_recording(tmp_path_factory, mode)
    root = Path(shutil.copytree(original, tmp_path / "clone"))
    assert save(root, original, original.parent)["provenance"]["status"] == "verified"
    name = "app/plugin.config.py" if mode == "cached_dotted" else "app/plugin.py"
    saved = json.loads((root / ".anaxigraph-v2/test-records/app.json").read_text())
    assert name not in saved["unloaded"]
    assert saved["executed"][name]
    if mode == "cached_dotted":
        assert "app/plugin.py" in saved["unloaded"]
        sibling = root / "app/plugin.py"
        sibling.write_text("VALUE = 999\n")
        [unrelated] = Application(root).tests_for_change()["suites"]
        assert unrelated["status"] != "all"
        assert "tests/test_first.py" not in unrelated["test_files"]
        sibling.write_text("VALUE = 99\n")
    (root / name).write_text("VALUE = 17\n")
    [selected] = Application(root).tests_for_change()["suites"]
    report = tmp_path / "mutated.xml"
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", f"--junitxml={report}"],
        cwd=root,
        env=os.environ | {"PYTHONPATH": str(root), "MODE": mode},
        text=True,
        capture_output=True,
    )
    assert done.returncode == 1, done.stdout + done.stderr
    failed = set(junit_cases([report], ".", saved["files"])[1])
    assert failed == set(CLIENTS)
    assert failed <= set(selected["test_files"])


def test_child_code_and_data_reach_cached_callers_but_unrelated_work_stays_local(
    recorded, tmp_path
):
    original, mode = recorded
    root = Path(shutil.copytree(original, tmp_path / "clone"))
    assert save(root, original, original.parent)["provenance"]["status"] == "verified"
    app = Application(root)
    opaque = mode not in {"normal", "cross_cached", "bytes_env", "outside", "parallel"}
    (root / "app/other.py").write_text("def other():\n    return 43\n")
    [suite] = app.tests_for_change()["suites"]
    if mode in {"parallel", "customization"}:
        assert (
            suite["status"] == "all"
        )  # Unobserved controller or parent startup stays conservative.
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
        if mode in {"parallel", "customization"} or (
            opaque and name == "app/value.txt" and mode != "descendant"
        ):
            assert suite["status"] == "all"
        else:
            assert suite["status"] == "selected", suite
            assert suite["test_files"] == CLIENTS
        (root / name).write_text((original / name).read_text())


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "truncated",
        "wrong_session",
        "late",
        "bad_shape",
        "bad_time",
        "foreign_root",
        "wrong_format",
    ],
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
        elif problem == "wrong_format":
            row["format"] = "anaxigraph-reads-v1"
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


@pytest.mark.parametrize("mode", ["startup_hook", "parent_startup"])
def test_startup_hooks_cannot_hide_failing_consumers(tmp_path_factory, tmp_path, mode):
    original, _ = make_recording(tmp_path_factory, mode)
    root = Path(shutil.copytree(original, tmp_path / "clone"))
    save(root, original, original.parent)
    (root / "app/value.txt").write_text("8")
    [suite] = Application(root).tests_for_change()["suites"]
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ANAXIGRAPH_", "COV_CORE_", "COVERAGE_", "PYTEST_XDIST_"))
    }
    python = str(original.parent / "startup-env/bin/python")
    environment.update(
        PYTHONPATH=str(root) + os.pathsep + str(Path(pytest.__file__).parent.parent),
        MODE=mode,
        CHILD_PYTHON=python,
        STARTUP_INPUT=str(root / "app/value.txt"),
    )
    from xml.etree import ElementTree

    report = tmp_path / "mutant.xml"
    done = subprocess.run(
        [
            python if mode == "parent_startup" else sys.executable,
            "-m",
            "pytest",
            "tests",
            "-q",
            f"--junitxml={report}",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 1, done.stdout + done.stderr
    failing = {
        "tests/" + case.attrib["name"] + ".py"
        for case in ElementTree.parse(report).iter("testcase")
        if case.find("failure") is not None
    }
    assert failing == {*CLIENTS, "tests/test_other.py"}
    assert failing <= set(suite["test_files"]), suite


@pytest.mark.parametrize("trigger", ["COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG", "active"])
def test_reviewed_coverage_startup_is_qualified_only_when_inactive(monkeypatch, trigger):
    from coverage.control import process_startup

    from anaxigraph.recorder.anaxigraph_audit import _opaque_pth

    reviewed = b'import sys; exec(\'import os\\n\\nif os.getenv("COVERAGE_PROCESS_START") or os.getenv("COVERAGE_PROCESS_CONFIG"):\\n try:\\n  import coverage\\n except:\\n  pass\\n else:\\n  coverage.process_startup(slug="pth")\')\n'
    monkeypatch.delenv("COVERAGE_PROCESS_START", raising=False)
    monkeypatch.delenv("COVERAGE_PROCESS_CONFIG", raising=False)
    monkeypatch.delattr(process_startup, "coverage", raising=False)
    assert not _opaque_pth(reviewed)
    assert not _opaque_pth(b"# Path only\n/project/source\n")
    assert _opaque_pth(reviewed + b"print('extra startup code')\n")
    if trigger == "active":
        monkeypatch.setattr(process_startup, "coverage", object(), raising=False)
    else:
        monkeypatch.setenv(trigger, "configured")
    assert _opaque_pth(reviewed)


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
