"""Real Git publication and non-writing structural scans, without network services."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_portable import annotate_module

from anaxigraph.application import Application
from anaxigraph.storage.portable import record_status


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def repository(root: Path) -> Application:
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Publication Test")
    git(root, "config", "user.email", "publication@example.test")
    (root / ".gitignore").write_text(".anaxigraph-v2/\n")
    (root / "billing.py").write_text("def amount(value):\n    return round(value, 2)\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial source")
    return Application(root)


def test_plain_scans_do_not_publish_or_modify_the_worktree(tmp_path):
    app = repository(tmp_path / "repo")
    for _ in range(3):
        result = app.scan()
        assert result["mapped_locally"] and not result["record_exported"]
        assert not (app.root / "anaxigraph").exists()
        assert git(app.root, "status", "--porcelain") == ""
    assert app.scan(publish=True)["record_exported"]
    assert (app.root / "anaxigraph" / "record.json").is_file()


def test_cli_scan_export_is_explicit_and_ordinary_scan_keeps_it_unchanged(tmp_path, capsys):
    from anaxigraph.interfaces.cli import main

    app = repository(tmp_path / "repo")
    assert main(["scan", "--repo", str(app.root)]) == 0
    assert json.loads(capsys.readouterr().out)["record_exported"] is False
    assert not (app.root / "anaxigraph").exists()
    assert main(["scan", "--repo", str(app.root), "--export"]) == 0
    assert json.loads(capsys.readouterr().out)["record_exported"] is True
    record = app.root / "anaxigraph/record.json"
    before = record.read_bytes(), record.stat().st_mtime_ns
    assert main(["scan", "--repo", str(app.root)]) == 0
    assert json.loads(capsys.readouterr().out)["record_exported"] is False
    assert (record.read_bytes(), record.stat().st_mtime_ns) == before


def test_git_states_distinguish_export_commit_and_last_known_push(tmp_path):
    app = repository(tmp_path / "repo")
    app.scan()
    assert not app.portable_status()["committed"]
    exported = app.export_record()
    assert exported["record_exported"] and not exported["tracked"]
    assert not exported["committed"] and exported["pushed"] is None
    git(app.root, "add", "anaxigraph/record.json")
    git(app.root, "commit", "-qm", "save record")
    status = app.portable_status()
    assert status["tracked"] and status["committed"]
    assert status["pushed"] is None and status["committed_pushed"] is None
    remote = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-q", str(remote))
    git(app.root, "remote", "add", "origin", str(remote))
    git(app.root, "push", "-qu", "origin", "main")
    status = app.portable_status()
    assert status["committed"] and status["pushed"] and status["committed_pushed"]
    assert status["publication"] == {
        "basis": "local_tracking_ref",
        "remote_contacted": False,
        "upstream_ref": "refs/remotes/origin/main",
        "head_in_upstream": True,
        "record_in_upstream": True,
    }
    with (app.root / "anaxigraph/record.json").open("a") as stream:
        stream.write("\n")
    dirty = app.portable_status()
    assert not dirty["committed"] and not dirty["pushed"]


def test_missing_remote_refs_and_non_git_checkout_remain_unknown(tmp_path):
    app = repository(tmp_path / "repo")
    app.scan(publish=True)
    git(app.root, "add", "anaxigraph/record.json")
    git(app.root, "commit", "-qm", "record")
    git(app.root, "remote", "add", "origin", str(tmp_path / "unavailable.git"))
    git(app.root, "config", "branch.main.remote", "origin")
    git(app.root, "config", "branch.main.merge", "refs/heads/main")
    assert app.portable_status()["committed"]
    assert app.portable_status()["pushed"] is None
    outside = tmp_path / "outside"
    outside.mkdir()
    assert record_status(outside)["committed"] is None
    assert record_status(outside)["pushed"] is None


def test_diverged_head_does_not_claim_the_current_checkout_was_pushed(tmp_path):
    app = repository(tmp_path / "repo")
    app.scan(publish=True)
    git(app.root, "add", "anaxigraph/record.json")
    git(app.root, "commit", "-qm", "record")
    remote = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-q", "-b", "main", str(remote))
    git(app.root, "remote", "add", "origin", str(remote))
    git(app.root, "push", "-qu", "origin", "main")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(remote), str(other))
    git(other, "config", "user.name", "Other")
    git(other, "config", "user.email", "other@example.test")
    (other / "remote.py").write_text("remote = True\n")
    git(other, "add", ".")
    git(other, "commit", "-qm", "remote change")
    git(other, "push", "-q", "origin", "main")
    (app.root / "local.py").write_text("local = True\n")
    git(app.root, "add", "local.py")
    git(app.root, "commit", "-qm", "local change")
    git(app.root, "fetch", "-q", "origin")
    status = app.portable_status()
    assert status["committed"] and not status["pushed"]
    assert status["publication"]["record_in_upstream"]
    assert not status["publication"]["head_in_upstream"]


def test_record_only_commit_and_fresh_clone_keep_canonical_export_bytes(tmp_path):
    app = repository(tmp_path / "repo")
    app.scan()
    annotate_module(app)
    request = app.analysis_request("patterns", ["billing.py"])
    from test_application import pattern_result

    app.submit_analysis(request["request_id"], pattern_result(request))
    app.export_record()
    path = app.root / "anaxigraph/record.json"
    initial = path.read_bytes()
    git(app.root, "add", "anaxigraph/record.json")
    git(app.root, "commit", "-qm", "publish saved understanding")
    app.scan()
    assert not app.export_record()["changed"]
    assert path.read_bytes() == initial
    assert git(app.root, "status", "--porcelain") == ""
    target = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(app.root), str(target))
    fresh = Application(target)
    fresh.scan()
    assert fresh.status()["annotations"]["current"] == 1
    assert not fresh.export_record()["changed"]
    assert (target / "anaxigraph/record.json").read_bytes() == initial


def test_unchanged_export_refreshes_git_publication_after_push(tmp_path):
    app = repository(tmp_path / "repo")
    app.scan(publish=True)
    git(app.root, "add", "anaxigraph/record.json")
    git(app.root, "commit", "-qm", "record")
    app.scan()
    before = app.export_record()
    assert not before["changed"] and before["committed"] and before["pushed"] is None
    remote = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-q", str(remote))
    git(app.root, "remote", "add", "origin", str(remote))
    git(app.root, "push", "-qu", "origin", "main")
    after = app.export_record()
    assert not after["changed"] and after["committed"] and after["pushed"]


def test_plain_scan_preserves_incompatible_records_without_losing_source(tmp_path):
    app = repository(tmp_path / "repo")
    folder = app.root / "anaxigraph"
    folder.mkdir()
    marker = folder / "record.json"
    original = '{"format":"anaxigraph-record-v3"}\n'
    marker.write_text(original)
    (folder / "__init__.py").write_text("def useful():\n    return True\n")
    (folder / "mapping").mkdir()
    (folder / "mapping/old.json").write_text('{"old":"derived cache"}')
    (folder / "mapping/useful.py").write_text("def retained():\n    return True\n")
    result = app.scan()
    assert result["modules"] == 3
    assert result["portable_import"]["compatible"] is False
    assert marker.read_text() == original
    assert "anaxigraph/mapping/old.json" not in app._current().snapshot.files
    assert "anaxigraph/mapping/useful.py" in app._current().snapshot.files
    with pytest.raises(ValueError, match="Unsupported"):
        app.export_record()
    assert marker.read_text() == original


def test_root_package_survives_explicit_portable_publication(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "anaxigraph").mkdir()
    (root / "anaxigraph/__init__.py").write_text("from .service import run\n")
    (root / "anaxigraph/service.py").write_text("def run():\n    return True\n")
    app = Application(root)
    first = app.scan()
    assert first["modules"] == 2
    identity = first["snapshot"]["id"]
    app.export_record()
    assert app.scan()["snapshot"]["id"] == identity
    assert (root / "anaxigraph/service.py").read_text().startswith("def run")
    assert set(json.loads((root / "anaxigraph/record.json").read_text())["source"]["files"]) == {
        "anaxigraph/__init__.py",
        "anaxigraph/service.py",
    }
