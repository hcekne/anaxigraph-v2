"""Durable coding-session evidence survives interrupted work without real model calls."""

import concurrent.futures
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runner  # noqa: E402


@pytest.fixture(autouse=True)
def synthetic_executor(monkeypatch):
    monkeypatch.setattr(runner.executor_policy, "preflight", lambda: {"synthetic": True})


def test_model_events_reach_disk_before_session_finishes(tmp_path, monkeypatch):
    script = tmp_path / "fake_model.py"
    release = tmp_path / "release"
    script.write_text(
        "import json,time\nfrom pathlib import Path\n"
        "print(json.dumps({'type':'turn.started'}),flush=True)\n"
        f"while not Path({str(release)!r}).exists(): time.sleep(.02)\n"
        "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':2,'output_tokens':1}}),flush=True)\n"
    )
    monkeypatch.setattr(runner, "codex_argv", lambda *_args: [sys.executable, str(script)])
    config = SimpleNamespace(agent="codex", agent_timeout=20)
    log = tmp_path / "step.jsonl"
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run_agent, config, tmp_path, log, "Synthetic request", False)
        try:
            deadline = time.monotonic() + 10
            while (not log.exists() or not log.stat().st_size) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert log.exists() and '"turn.started"' in log.read_text()
            assert not future.done()
            journal = json.loads(log.with_suffix(".session.json").read_text())
            assert journal["status"] == "running"
            assert journal["pid"] > 0
            assert journal["process_start"] and journal["boot_id"]
        finally:
            release.touch()
        result = future.result(timeout=20)
    assert result["status"] == "completed"
    assert result["usage"]["input_tokens"] == 2
    assert result["usage_unknown_sessions"] == 0
    assert json.loads(log.with_suffix(".session.json").read_text())["status"] == "completed"
    before = log.read_bytes()
    with pytest.raises(FileExistsError, match="already exists"):
        runner.run_agent(config, tmp_path, log, "Different request", False)
    assert log.read_bytes() == before


def test_timed_out_model_keeps_partial_events_and_unknown_cost(tmp_path, monkeypatch):
    script = tmp_path / "fake_model.py"
    script.write_text("import time\nprint('partial model event',flush=True)\ntime.sleep(30)\n")
    monkeypatch.setattr(runner, "codex_argv", lambda *_args: [sys.executable, str(script)])
    config = SimpleNamespace(agent="codex", agent_timeout=0.5)
    log = tmp_path / "step.jsonl"
    result = runner.run_agent(config, tmp_path, log, "Synthetic timeout", False)
    assert result["status"] == "timeout"
    assert result["usage_unknown_sessions"] == 1
    assert "partial model event" in log.read_text()
    assert json.loads(log.with_suffix(".session.json").read_text())["status"] == "timeout"


def test_failed_checkpoint_replace_keeps_last_valid_result(tmp_path, monkeypatch):
    path = tmp_path / "steps.jsonl"
    runner.append_jsonl(path, {"step": 0})
    original = path.read_bytes()

    def fail(*_args, **_kwargs):
        raise OSError("Synthetic interrupted checkpoint")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="interrupted checkpoint"):
        runner.append_jsonl(path, {"step": 1})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
