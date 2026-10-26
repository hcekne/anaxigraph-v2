"""Task lifecycle and source-blind prose regressions from independent usage."""

from __future__ import annotations

import sqlite3
import subprocess

import pytest

from anaxigraph.analysis.packets import _check_behaviors
from anaxigraph.application import Application
from anaxigraph.indexing import index
from anaxigraph.indexing.python import parse_module
from anaxigraph.storage import Store


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
    backup = app.store.path.with_name(app.store.path.name + ".pre-v6.sqlite")
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert db.execute("SELECT snapshot_id FROM baselines").fetchone()[0] == before
