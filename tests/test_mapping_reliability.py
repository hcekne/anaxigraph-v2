"""SQLite mapping concurrency and durable failure diagnostics, without model calls."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_workflows import answer, app

from anaxigraph.application import Application
from anaxigraph.workflows.engine import WorkflowRunner


def test_32_workers_prepare_and_submit_real_sqlite_mapping_requests(tmp_path):
    for index in range(96):
        (tmp_path / f"unit{index}.py").write_text(
            "".join(
                f"def operation_{index}_{function}(value):\n    return value + {function}\n\n"
                for function in range(16)
            )
        )
    application = Application(tmp_path)
    application.scan()
    ready = threading.Barrier(32)

    def map_module(index):
        if index < 32:
            ready.wait(timeout=10)
        request = application.analysis_request("annotate", [f"module:unit{index}.py"])
        return application.submit_analysis(request["request_id"], answer(request))["saved"]

    with ThreadPoolExecutor(max_workers=32) as pool:
        assert all(pool.map(map_module, range(96)))
    assert application.status()["annotations"]["current"] == 96
    with sqlite3.connect(application.store.path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_writer_gate_coordinates_processes_and_allows_readers(tmp_path):
    application = app(tmp_path, count=1)
    application.scan()
    request = application.analysis_request("annotate")
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; from anaxigraph.storage import Store; "
            "store=Store(Path(sys.argv[1])); "
            "\nwith store._connection(write=True):\n print('locked', flush=True); input()",
            str(application.store.path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        with ThreadPoolExecutor(max_workers=1) as pool:
            started = threading.Event()

            def submit():
                started.set()
                return application.submit_analysis(request["request_id"], answer(request))

            pending = pool.submit(submit)
            assert started.wait(5)
            try:
                # The other process owns only our writer gate, so a read still succeeds.
                assert application.store.load_graph(str(tmp_path)) is not None
                with pytest.raises(TimeoutError):
                    pending.result(timeout=0.1)
            finally:
                child.communicate("release\n", timeout=5)
            assert pending.result(timeout=5)["saved"] is True
        assert child.returncode == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()


def test_32_competing_results_keep_optimistic_annotation_check(tmp_path):
    application = app(tmp_path, count=1)
    application.scan()
    request = application.analysis_request("annotate")
    ready = threading.Barrier(32)

    def submit(index):
        result = answer(request) | {"producer": f"synthetic-competing-worker-{index}"}
        ready.wait(timeout=10)
        try:
            return application.submit_analysis(request["request_id"], result)["saved"]
        except ValueError as error:
            assert "newer result was already saved" in str(error)
            return False

    with ThreadPoolExecutor(max_workers=32) as pool:
        assert sum(pool.map(submit, range(32))) == 1
    assert application.status()["annotations"]["current"] == 1


@pytest.mark.parametrize(
    ("change", "counts"),
    [
        ("missing", "expected=1 received=0 missing=1 duplicate=0 unexpected=0"),
        ("duplicate", "expected=1 received=2 missing=0 duplicate=1 unexpected=0"),
        ("unexpected", "expected=1 received=1 missing=1 duplicate=0 unexpected=1"),
    ],
)
def test_function_coverage_diagnoses_ids_and_preserves_last_map(tmp_path, change, counts):
    application = app(tmp_path, count=1)
    application.scan()
    request = application.analysis_request("annotate")
    valid = answer(request)
    # Grammar is not the coverage contract: multiple sentences do not lose an identity.
    valid["annotations"][0]["functions"][0]["overview"] = "Return a value. Preserve its type."
    application.submit_analysis(request["request_id"], valid)
    request = application.analysis_request("annotate")
    result = answer(request)
    functions = result["annotations"][0]["functions"]
    if change == "missing":
        functions.clear()
    elif change == "duplicate":
        functions.append(dict(functions[0]))
    else:
        functions[0]["node_id"] = "symbol:invented.py#missing"
    before = application.store.path.read_bytes()
    with pytest.raises(ValueError, match=counts):
        application.submit_analysis(request["request_id"], result)
    assert application.store.path.read_bytes() == before


def test_failure_history_survives_12_run_rotation_with_sqlite_codes(tmp_path, monkeypatch):
    application = app(tmp_path, count=1)
    # Obtain SQLite's real structured diagnostic, rather than manufacturing an attribute.
    diagnostic = tmp_path / "diagnostic.sqlite"
    with sqlite3.connect(diagnostic) as owner, sqlite3.connect(diagnostic, timeout=0) as contender:
        owner.execute("CREATE TABLE private_diagnostic (value)")
        owner.execute("INSERT INTO private_diagnostic VALUES (1)")
        try:
            contender.execute("INSERT INTO private_diagnostic VALUES (2)")
        except sqlite3.OperationalError as error:
            failure = error

    def unavailable(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(application.store, "annotate", unavailable)
    runner = WorkflowRunner(application, lambda request, *_args, **_kwargs: answer(request))
    config = {"stages": {"annotate": {"provider": "codex", "model": "synthetic"}}}
    first = runner.start("mapping", config, background=False)
    assert first["failed_attempts"] == 1
    entry = first["failure_history"]["items"][0]
    assert entry["error_kind"] == "OperationalError"
    assert entry["sqlite_errorname"] == "SQLITE_BUSY"
    assert entry["sqlite_errorcode"] == sqlite3.SQLITE_BUSY
    assert entry["worker"]["model"] == "synthetic"
    for _ in range(12):
        runner.start("mapping", config, background=False)
    assert not runner._path(first["run_id"]).exists()
    status = WorkflowRunner(application).status(batch_offset=10, batch_limit=10)
    assert status["total"] == 12
    history = status["failure_history"]
    assert history["total"] == history["retained"] == 13
    assert history["scope"] == "checkout"
    assert entry in history["items"]
    assert len(list(runner.directory.glob("*.json"))) == 13  # 12 progress + one ledger.
    assert "private_diagnostic" not in (runner.directory / "failures.json").read_text()


def test_failure_history_is_bounded_and_does_not_store_raw_provider_output(tmp_path):
    application = app(tmp_path, count=1)
    runner = WorkflowRunner(application)
    run = {
        "id": "a" * 32,
        "batches": [],
    }
    batch = {"id": 1, "kind": "annotate", "attempts": 1}
    for index in range(270):
        batch["attempts"] = index + 1
        runner._record_failure(
            run, batch, RuntimeError("PRIVATE_RAW_SOURCE_AND_PROMPT"), "provider"
        )
    history = runner._failure_history(250, 10)
    assert history["total"] == 270
    assert history["retained"] == history["retention_limit"] == 256
    assert len(history["items"]) == 6
    assert history["next_offset"] is None
    path = runner.directory / "failures.json"
    assert path.stat().st_size < 200_000
    assert "PRIVATE_" not in path.read_text()
    assert len(json.loads(path.read_text())["items"]) == 256
