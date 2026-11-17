"""Task lifecycle and source-blind prose regressions from independent usage."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anaxigraph.analysis.packets import _check_behaviors
from anaxigraph.application import Application
from anaxigraph.indexing import index
from anaxigraph.indexing.python import parse_module
from anaxigraph.interfaces import cli
from anaxigraph.interfaces.http import create_app
from anaxigraph.interfaces.mcp import create_mcp
from anaxigraph.storage import Store
from anaxigraph.storage.schema import SCHEMA_VERSION


def application(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "service.py").write_text("def status():\n    return 1\n")
    return Application(root)


def test_new_task_rebases_without_hiding_same_task_followup_regression(tmp_path):
    app = application(tmp_path)
    first = app.scan()["snapshot"]["id"]
    assert app.prepare_change("Grow invoice service")["baseline_id"] == first
    source = app.root / "service.py"
    source.write_text("\n".join(f"value_{n} = {n}" for n in range(612)))
    app.scan()
    assert app.prepare_change("  GROW  invoice\nservice ")["baseline_id"] == first
    assert any(f["change"] == "introduced" for f in app.validate_change()["findings"])
    current = app.status()["snapshot"]["id"]
    second = app.prepare_change("Add a different feature")
    assert second["baseline_id"] == current != first
    assert not any(f["change"] == "introduced" for f in app.validate_change()["findings"])
    assert (
        app.prepare_change("Continue under another label", continue_task=True)["baseline_id"]
        == current
    )


def test_new_head_rebases_unless_explicitly_continuing(tmp_path):
    app = application(tmp_path)

    def git(*args):
        return subprocess.run(["git", "-C", str(app.root), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    git("add", "service.py")
    git("commit", "-qm", "Initial")
    initial = app.scan()["snapshot"]["id"]
    assert app.prepare_change("Implement export")["baseline_id"] == initial
    git("commit", "--allow-empty", "-qm", "Advance HEAD")
    current = app.scan()["snapshot"]["id"]
    assert app.prepare_change("Implement export")["baseline_id"] == current != initial
    git("commit", "--allow-empty", "-qm", "Continue HEAD")
    app.scan()
    assert app.prepare_change("Implement export", continue_task=True)["baseline_id"] == current


def test_plan_rewording_cannot_rebase_an_in_progress_change(tmp_path):
    app = application(tmp_path)
    app.scan()
    initial = app.prepare_change("Improve service")
    (app.root / "service.py").write_text("\n".join(f"value_{n} = {n}" for n in range(612)))
    app.scan()
    plan = app.assess_plan("Improve the implementation of the service", ["service.py"])
    assert plan["baseline_id"] == initial["baseline_id"]
    assert plan["task_id"] == initial["task_id"]
    assert any(f["change"] == "introduced" for f in app.validate_change()["findings"])
    assert app.prepare_change("Begin a separate feature")["baseline_id"] != initial["baseline_id"]


@pytest.mark.parametrize("interface", ["application", "cli", "mcp"])
def test_current_baseline_captures_working_files_and_then_stays_pinned(tmp_path, capsys, interface):
    app = application(tmp_path)
    task = "Improve service"
    initial = app.prepare_change(task)["baseline_id"]
    source = app.root / "service.py"
    source.write_text("\n".join(f"value_{n} = {n}" for n in range(612)))
    assert app.status()["snapshot"]["id"] == initial  # Saved status does not refresh source.
    if interface == "application":
        fresh = app.prepare_change(task, baseline_id="current")
    elif interface == "cli":
        assert cli.main(["prepare", task, "--repo", str(app.root), "--baseline-id", "current"]) == 0
        fresh = json.loads(capsys.readouterr().out)
    else:

        async def call():
            result = await create_mcp(app).call_tool(
                "prepare_change", {"task": task, "baseline_id": "current"}
            )
            return json.loads(result[0].text)

        fresh = asyncio.run(call())
    assert fresh["baseline_id"] == fresh["snapshot_id"] != initial
    source.write_text(source.read_text() + "\nfollowup = 1\n")
    followup = app.prepare_change(task)
    assert followup["baseline_id"] == fresh["baseline_id"] != followup["snapshot_id"]
    assert any(f["change"] == "worsened" for f in app.validate_change()["findings"])


@pytest.mark.parametrize("logging", [True, False])
def test_explicit_current_baseline_resets_the_local_plan_even_without_source_edits(
    tmp_path, logging
):
    app = application(tmp_path)
    app.evidence.enabled = logging
    assessed = app.assess_plan("Improve service", ["service.py"])
    app.evidence.record(
        "architecture_plan",
        {
            "baseline_id": assessed["baseline_id"],
            "snapshot_id": assessed["snapshot_id"],
            "status": "decisions_recorded",
            "metrics": {"obligations": []},
        },
    )
    assert app.validate_change()["architecture"]["status"] != "not_assessed"
    fresh = app.prepare_change("Improve service", baseline_id="current", continue_task=True)
    assert fresh["baseline_id"] == assessed["baseline_id"]
    if logging:
        assert fresh["task_id"] != assessed["task_id"]
        assert app.evidence.latest("architecture_plan") is None
        assert app.evidence.latest("task_started")["continued"] is False
    assert app.validate_change()["architecture"]["status"] == "not_assessed"


def test_current_baseline_stays_with_its_captured_source_when_another_process_scans(
    tmp_path, monkeypatch
):
    app = application(tmp_path)
    task = "Improve service"
    app.prepare_change(task)
    source = app.root / "service.py"
    source.write_text(source.read_text() + "\nfirst = 1\n")
    other = Application(app.root)
    original = app.store.pin_baseline

    def interleave(*args, **kwargs):
        captured = other.store.current_id(str(app.root))
        assert args[1] == captured
        source.write_text(source.read_text() + "\nsecond = 2\n")
        other.scan()
        assert other.store.current_id(str(app.root)) != captured
        return original(*args, **kwargs)

    monkeypatch.setattr(app.store, "pin_baseline", interleave)
    prepared = app.prepare_change(task, baseline_id="current")
    assert prepared["baseline_id"] == prepared["snapshot_id"]
    assert prepared["baseline_id"] != other.status()["snapshot"]["id"]


def test_dashboard_refreshes_a_new_baseline_in_one_request_and_preserves_normal_preparation(
    tmp_path,
):
    app = application(tmp_path)
    task = "Improve service"
    initial = app.prepare_change(task)["baseline_id"]
    client = TestClient(create_app(app), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    source = app.root / "service.py"
    source.write_text(source.read_text() + "\nfirst_change = 1\n")
    assert client.get("/api/status").json()["snapshot"]["id"] == initial
    fresh = client.post("/api/prepare", json={"task": task, "baseline_id": "current"})
    assert fresh.status_code == 200, fresh.text
    fresh = fresh.json()
    assert fresh["baseline_id"] == fresh["snapshot_id"] != initial
    source.write_text(source.read_text() + "\nfollowup = 2\n")
    normal = client.post("/api/prepare", json={"task": task}).json()
    assert normal["baseline_id"] == fresh["baseline_id"] != normal["snapshot_id"]
    restarted = client.post("/api/prepare", json={"task": task, "baseline_id": "current"}).json()
    assert restarted["baseline_id"] == restarted["snapshot_id"] == normal["snapshot_id"]
    packets = tmp_path / "prepare-packets.json"
    packets.write_text(json.dumps([fresh, normal, restarted]))
    script = Path(__file__).with_name("prepare_dashboard.cjs")
    done = subprocess.run(
        ["node", str(script), str(packets)], capture_output=True, text=True, check=True
    )
    assert json.loads(done.stdout)["requests"] == 3


def test_dictionary_words_remain_valid_behavior_without_an_allowlist(tmp_path):
    app = application(tmp_path)
    (app.root / "service.py").write_text(
        "def status(): pass\ndef modules(): pass\ndef path(): pass\n"
        "def record(): pass\ndef scan(): pass\nclass Store: pass\n"
    )
    graph = index(app.root)
    _check_behaviors(
        graph,
        [
            "reports status for each module",
            "search modules by path",
            "a record is exported after each scan",
            "Scan the results for changes.",
            "Store records after validating input.",
        ],
    )


@pytest.mark.parametrize(
    "behavior",
    [
        "Delegate to InvoiceStore.",
        "Call create_invoice for this task.",
        "Call status() first.",
        "Use `record` for persistence.",
        "Call Store.save to persist.",
        "Query system.status for readiness.",
        "Look at service.py for details.",
    ],
)
def test_code_shaped_source_identities_are_rejected(tmp_path, behavior):
    app = application(tmp_path)
    (app.root / "service.py").write_text(
        "def status(): pass\ndef record(): pass\ndef create_invoice(): pass\n"
        "class Store: pass\nclass InvoiceStore: pass\n"
    )
    with pytest.raises(ValueError, match="source identity"):
        _check_behaviors(index(app.root), [behavior])


def test_interface_identity_ignores_bodies_but_tracks_public_contracts():
    source = 'def operation(value: int = 1) -> int:\n    """Normalize input."""\n    return value\n'

    def data(text):
        return parse_module("service.py", text, "fingerprint")[1].data

    original = data(source)
    body = data(source.replace("return value", "return value + 1"))
    assert body["interface_fingerprint"] == original["interface_fingerprint"]
    assert body["semantic_fingerprint"] != original["semantic_fingerprint"]
    for changed in [
        source.replace("= 1", "= 2"),
        source.replace("Normalize input", "Reject invalid input"),
        source + "\ndef additional():\n    return 1\n",
    ]:
        assert data(changed)["interface_fingerprint"] != original["interface_fingerprint"]
    hidden = '__all__ = ["operation"]\n' + source
    assert (
        data(hidden)["interface_fingerprint"]
        == data(hidden + "\ndef helper(): return 2\n")["interface_fingerprint"]
    )


def test_version_five_baseline_is_preserved_in_backed_up_migration(tmp_path):
    app = application(tmp_path)
    app.scan()
    before = app.store.pin_baseline(str(app.root))
    with sqlite3.connect(app.store.path) as db:
        db.executescript("""
            CREATE TABLE baseline_v5(root TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL,
                FOREIGN KEY(root,snapshot_id) REFERENCES snapshots(root,id));
            INSERT INTO baseline_v5 SELECT root,snapshot_id FROM baselines;
            DROP TABLE baselines;
            ALTER TABLE baseline_v5 RENAME TO baselines;
            PRAGMA user_version=5;
        """)
    upgraded = Store(app.store.path)
    assert upgraded.pin_baseline(str(app.root), continue_task=True) == before
    backup = app.store.path.with_name(app.store.path.name + f".pre-v{SCHEMA_VERSION}.sqlite")
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert db.execute("SELECT snapshot_id FROM baselines").fetchone()[0] == before
