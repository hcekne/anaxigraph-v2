"""Verification budgets stop faulty work and its children without changing model calls."""

import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import process_guard as guard  # noqa: E402


def running(pid: int) -> bool:
    try:
        return (Path("/proc") / str(pid) / "stat").read_text().rpartition(")")[2].split()[0] != "Z"
    except FileNotFoundError:
        return False


def assert_stopped(pid: int) -> None:
    deadline = time.monotonic() + 2
    while running(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not running(pid), f"Verification child {pid} survived cleanup"


def cleanup_test_children(pids: list[int]) -> None:
    for pid in pids:
        if running(pid):
            os.kill(pid, signal.SIGKILL)


def test_normal_result_retains_arguments_text_stdin_environment_and_exit_status(tmp_path):
    argv = [
        sys.executable,
        "-c",
        "import os,sys; print(sys.stdin.read()+os.environ['GUARD_FIXTURE']); "
        "print('reported error',file=sys.stderr);sys.exit(7)",
    ]
    result = guard.run(argv, tmp_path, 10, {"GUARD_FIXTURE": "!"}, "héllo")
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.args == argv and result.returncode == 7
    assert result.stdout == "héllo!\n" and result.stderr == "reported error\n"
    assert result.resource_usage["termination_reason"] == "completed"
    assert result.resource_usage["policy"] == guard.RESOURCE_POLICY
    assert result.resource_usage["output_truncated"] is False


@pytest.mark.parametrize("escaped", [False, True])
def test_timeout_kills_infinite_children_even_when_they_ignore_sigterm(tmp_path, escaped):
    script = tmp_path / "tree.py"
    identities = tmp_path / "pids.json"
    script.write_text(
        "import json,os,signal,subprocess,sys,time\n"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        "child = subprocess.Popen([sys.executable,'-c',"
        "'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(100)'],"
        f"start_new_session={escaped!r})\n"
        f"open({str(identities)!r},'w').write(json.dumps([os.getpid(),child.pid]))\n"
        "print('started',flush=True)\n"
        "print('before timeout',file=sys.stderr,flush=True)\n"
        "time.sleep(100)\n"
    )
    pids = []
    try:
        result = guard.run([sys.executable, str(script)], tmp_path, 0.8)
        pids = json.loads(identities.read_text())
        assert result.returncode == 124
        assert result.resource_usage["termination_reason"] == "timeout"
        assert result.stdout == "started\n"
        assert "before timeout" in result.stderr and result.stderr.endswith("timeout")
        assert isinstance(result.stdout, str) and isinstance(result.stderr, str)
        for pid in pids:
            assert_stopped(pid)
    finally:
        cleanup_test_children(pids)


def test_memory_limit_measures_resident_children_without_limiting_virtual_addresses(tmp_path):
    child = tmp_path / "allocate.py"
    child.write_text(
        "import mmap,time\n"
        "reserve=mmap.mmap(-1,16*1024**3)\n"
        "data=[]\n"
        "while True:\n data.append(bytearray(1024**2));time.sleep(0.01)\n"
    )
    result = guard.run(
        [sys.executable, str(child)],
        tmp_path,
        10,
        rss_limit_bytes=24 * 1024**2,
        sample_interval_seconds=0.01,
    )
    assert result.returncode == 137
    assert result.resource_usage["termination_reason"] == "rss_limit"
    assert result.resource_usage["peak_sampled_rss_bytes"] > 24 * 1024**2
    assert result.resource_usage["policy"]["rss_limit_bytes"] == 24 * 1024**2
    assert "rss_limit" in result.stderr


@pytest.mark.parametrize("stdout_size,stderr_size", [(5000, 0), (700, 700)])
def test_output_spooled_to_files_and_bounded_even_when_process_exits_between_samples(
    tmp_path, stdout_size, stderr_size
):
    result = guard.run(
        [
            sys.executable,
            "-c",
            f"import sys;sys.stdout.write('x'*{stdout_size});sys.stderr.write('e'*{stderr_size})",
        ],
        tmp_path,
        10,
        output_limit_bytes=1024,
    )
    assert result.returncode == 137
    assert result.resource_usage["termination_reason"] == "output_limit"
    assert result.resource_usage["stdout_bytes"] == stdout_size
    assert result.resource_usage["stderr_bytes"] == stderr_size
    assert result.resource_usage["output_truncated"] is True
    assert result.stdout == "x" * min(stdout_size, 1024)
    assert "output_limit" in result.stderr


def test_successful_parent_cannot_leave_a_background_verification_child(tmp_path):
    child_pid = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(100)'],"
        "start_new_session=True);"
        f"open({str(child_pid)!r},'w').write(str(child.pid));time.sleep(0.2)"
    )
    pids = []
    try:
        result = guard.run([sys.executable, "-c", code], tmp_path, 10)
        pids = [int(child_pid.read_text())]
        assert result.returncode == 0
        assert pids[0] in result.resource_usage["cleanup_signaled_pids"]
        assert_stopped(pids[0])
    finally:
        cleanup_test_children(pids)


def test_missing_memory_samples_remain_unknown_instead_of_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "_sample", lambda *_args: None)
    result = guard.run([sys.executable, "-c", "print('done')"], tmp_path, 10)
    assert result.returncode == 0
    assert result.resource_usage["peak_sampled_rss_bytes"] is None
    assert result.resource_usage["rss_samples"] == 0


def test_memory_budget_accounts_for_a_hogging_descendant_and_removes_it(tmp_path):
    pid_file = tmp_path / "child.pid"
    child = "import time;data=[]\nwhile True:\n data.append(bytearray(1024**2));time.sleep(0.01)"
    parent = (
        "import subprocess,sys,time;"
        f"child=subprocess.Popen([sys.executable,'-c',{child!r}]);"
        f"open({str(pid_file)!r},'w').write(str(child.pid));time.sleep(100)"
    )
    pids = []
    try:
        result = guard.run(
            [sys.executable, "-c", parent],
            tmp_path,
            5,
            rss_limit_bytes=40 * 1024**2,
            sample_interval_seconds=0.01,
        )
        pids = [int(pid_file.read_text())]
        assert result.resource_usage["termination_reason"] == "rss_limit"
        assert result.returncode == 137
        assert_stopped(pids[0])
    finally:
        cleanup_test_children(pids)


def test_verification_guards_can_start_from_concurrent_harness_threads(tmp_path):
    def one(value):
        return guard.run(
            [sys.executable, "-c", f"import time;time.sleep(0.03);print({value})"], tmp_path, 5
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, range(4)))
    assert [result.stdout.strip() for result in results] == ["0", "1", "2", "3"]
    assert all(result.returncode == 0 for result in results)


def test_guard_is_available_through_both_package_and_script_metrics_imports(tmp_path):
    project = Path(__file__).resolve().parents[2]
    code = (
        "import pathlib,sys;from experiments.feature_drift import metrics;"
        "result=metrics.run([sys.executable,'-c',\"print('bounded')\"],pathlib.Path.cwd(),5);"
        "assert result.returncode==0;assert result.resource_usage['policy']==metrics.RESOURCE_POLICY;"
        "print(result.stdout,end='')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(project)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0 and result.stdout == "bounded\n", result.stderr
