"""Refresh survives browser requests without duplicate scans or cold graph materialization."""

import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Lock
from time import monotonic, sleep

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anaxigraph.analysis import behavior
from anaxigraph.application import Application
from anaxigraph.contracts import Node
from anaxigraph.interfaces.scan_jobs import _scan_routes


def application(tmp_path):
    (tmp_path / "component.py").write_text("# A comment\n\ndef operation():\n    return 1\n")
    app = Application(tmp_path)
    app.scan()
    return app


def settled(client):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        result = client.get("/api/scan/status").json()
        if result["status"] != "running":
            return result
        sleep(0.01)
    pytest.fail("Structural refresh did not settle")


def test_missing_annotations_do_not_compile_any_source_identity_matcher(tmp_path, monkeypatch):
    app = application(tmp_path)
    graph = app._current()
    external = tuple(
        Node(
            f"external:component.py#operation:unknown_{i}",
            "external",
            f"unknown_{i}",
            "component.py",
        )
        for i in range(2000)
    )
    graph = replace(graph, nodes=graph.nodes + external)
    monkeypatch.setattr(
        behavior,
        "behavior_matcher",
        lambda *_args: pytest.fail("Unannotated graph compiled a source identity matcher"),
    )
    assert behavior.annotation_behavior_problems(graph) == {}


def test_simultaneous_cold_readers_load_one_graph(tmp_path, monkeypatch):
    app = application(tmp_path)
    app._graph_cache = None
    original = app.store.load_graph
    entered, release, second_started = Event(), Event(), Event()
    calls = []

    def load(*args):
        calls.append(args)
        entered.set()
        assert release.wait(5)
        return original(*args)

    def second():
        second_started.set()
        return app._current()

    monkeypatch.setattr(app.store, "load_graph", load)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(app._current)
        try:
            assert entered.wait(5)
            other = pool.submit(second)
            assert second_started.wait(5)
            sleep(0.03)
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result(5) is other.result(5)


def test_sync_scans_serialize_and_preserve_real_phases(tmp_path, monkeypatch):
    app = application(tmp_path)
    original = app.indexer.scan
    entered, release, second_started = Event(), Event(), Event()
    active, maximum = 0, 0
    counter = Lock()

    def scan():
        nonlocal active, maximum
        with counter:
            active += 1
            maximum = max(maximum, active)
        entered.set()
        assert release.wait(5)
        try:
            return original()
        finally:
            with counter:
                active -= 1

    phases = []

    def other_scan():
        second_started.set()
        return app.scan()

    monkeypatch.setattr(app.indexer, "scan", scan)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(app.scan, progress=phases.append)
        try:
            assert entered.wait(5)
            second = pool.submit(other_scan)
            assert second_started.wait(5)
            sleep(0.03)
            assert maximum == 1
        finally:
            release.set()
        assert first.result(5)["snapshot"]["id"] == second.result(5)["snapshot"]["id"]
    assert maximum == 1
    assert phases == [
        "Reading source and resolving references",
        "Reusing saved understanding",
        "Refreshing map readiness",
    ]


def test_async_refresh_coalesces_and_reconnects_without_loading_readiness():
    entered, release = Event(), Event()
    calls = []

    class SlowApplication:
        def scan(self, *, progress):
            calls.append(True)
            progress("Resolving references")
            entered.set()
            assert release.wait(5)
            return {"snapshot": {"id": "synthetic-snapshot"}}

        def status(self):
            pytest.fail("Job polling loaded the cold graph")

    server = FastAPI()
    _scan_routes(server, SlowApplication())
    with TestClient(server) as first:
        assert first.get("/api/scan/status").json()["status"] == "idle"
        try:
            assert first.post("/api/scan/start").status_code == 202
            assert entered.wait(5)
            assert first.post("/api/scan/start").json()["status"] == "running"
            with TestClient(server) as reconnected:
                pending = reconnected.get("/api/scan/status").json()
                assert pending["phase"] == "Resolving references"
                assert pending["elapsed_seconds"] >= 0
            assert len(calls) == 1
        finally:
            release.set()
        completed = settled(first)
        assert completed["result"]["snapshot"]["id"] == "synthetic-snapshot"
        assert first.get("/api/scan/status").json()["status"] == "completed"
        assert len(calls) == 1


def test_failed_background_refresh_retains_the_last_saved_snapshot(tmp_path, monkeypatch):
    app = application(tmp_path)
    snapshot = app.status()["snapshot"]["id"]

    def fail():
        raise ValueError("Synthetic read failure")

    monkeypatch.setattr(app.indexer, "scan", fail)
    server = FastAPI()
    _scan_routes(server, app)
    with TestClient(server) as client:
        client.post("/api/scan/start")
        result = settled(client)
        assert result["status"] == "failed" and result["error"] == "Synthetic read failure"
    assert app.status()["snapshot"]["id"] == snapshot


def test_total_source_loc_covers_the_map_and_is_unknown_before_scan(tmp_path):
    app = Application(tmp_path)
    assert app.status()["source_loc"] is None
    (tmp_path / "unit.py").write_text("# comment\n\ndef run():\n    return 1\n")
    (tmp_path / "test_unit.py").write_text("def test_case():\n    assert True\n")
    app.scan()
    assert app.status()["source_loc"] == 4


def test_restart_unchanged_refresh_restores_cached_facts_without_parsing_or_reloading(
    tmp_path, monkeypatch
):
    original = application(tmp_path)
    restarted = Application(tmp_path)
    before = restarted.status()
    monkeypatch.setattr(
        restarted.store, "load_graph", lambda *_args: pytest.fail("Warm graph loaded twice")
    )
    result = restarted.scan()
    assert result["snapshot"]["id"] == before["snapshot"]["id"]
    assert result["indexing"]["modules_parsed"] == result["indexing"]["modules_resolved"] == 0
    assert original.status()["snapshot"]["id"] == result["snapshot"]["id"]


@pytest.mark.parametrize("changed", ["source", "revision", "exclusions"])
def test_restored_facts_are_not_reused_after_input_identity_changes(tmp_path, changed):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    if changed == "revision":
        git("init", "-q")
        git("config", "user.name", "Synthetic fixture")
        git("config", "user.email", "fixture@example.invalid")
    app = application(tmp_path)
    if changed == "revision":
        git("add", "component.py")
        git("commit", "-qm", "Initial source")
        app.scan()
    before = app.status()["snapshot"]["id"]
    if changed == "source":
        (tmp_path / "component.py").write_text("def operation():\n    return 2\n")
    elif changed == "revision":
        git("commit", "--allow-empty", "-qm", "New revision without source edits")
    exclusions = ("unmatched-future-pattern/**",) if changed == "exclusions" else ()
    restarted = Application(tmp_path, excluded=exclusions)
    result = restarted.scan()
    assert result["snapshot"]["id"] != before
    assert result["indexing"]["modules_parsed"] == result["indexing"]["modules_resolved"] == 1
