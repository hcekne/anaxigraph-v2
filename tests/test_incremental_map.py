"""An edit costs work in proportion to what it can affect, in this process or the next."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from anaxigraph.application import Application
from anaxigraph.indexing import index


def checkout(root: Path) -> Path:
    root.mkdir()
    (root / "store.py").write_text("LIMIT = 3\n\n\ndef save(item):\n    return item\n")
    (root / "service.py").write_text(
        "from store import save\n\n\ndef create(item):\n    return save(item)\n"
    )
    (root / "report.py").write_text("def render():\n    return 'report'\n")
    (root / "test_service.py").write_text(
        "from service import create\n\n\ndef test_create():\n    assert create(1) == 1\n"
    )
    (root / "notes.md").write_text("Notes.\n")
    return root


def part_rows(application: Application) -> set[tuple[str, str]]:
    with sqlite3.connect(application.store.path) as db:
        return set(db.execute("SELECT path, key FROM graph_parts"))


def test_an_edit_reads_one_file_and_links_only_what_depended_on_its_surface(tmp_path):
    root = checkout(tmp_path / "checkout")
    application = Application(root, tmp_path / "knowledge.sqlite")
    assert application.scan()["indexing"]["modules_parsed"] == 4
    before = part_rows(application)

    (root / "store.py").write_text("LIMIT = 4\n\n\ndef save(item):\n    return item\n")
    indexing = application.scan()["indexing"]
    assert (indexing["modules_parsed"], indexing["modules_resolved"]) == (1, 1)
    changed = part_rows(application) - before
    assert {path for path, _key in changed} == {"store.py"}

    # Renaming what others import is a change to its surface: its importer is linked again.
    (root / "store.py").write_text("LIMIT = 4\n\n\ndef keep(item):\n    return item\n")
    indexing = application.scan()["indexing"]
    assert (indexing["modules_parsed"], indexing["modules_resolved"]) == (1, 2)


def test_a_change_to_a_file_that_is_not_source_writes_no_part(tmp_path):
    root = checkout(tmp_path / "checkout")
    application = Application(root, tmp_path / "knowledge.sqlite")
    first = application.scan()["snapshot"]["id"]
    before = part_rows(application)
    (root / "notes.md").write_text("More notes.\n")
    result = application.scan()
    assert result["snapshot"]["id"] != first
    assert result["indexing"]["modules_parsed"] == result["indexing"]["modules_resolved"] == 0
    assert part_rows(application) == before


def test_a_new_process_resumes_from_saved_facts_and_reads_only_the_edited_file(tmp_path):
    root = checkout(tmp_path / "checkout")
    database = tmp_path / "knowledge.sqlite"
    first = Application(root, database).scan()
    (root / "report.py").write_text("def render():\n    return 'weekly report'\n")
    restarted = Application(root, database)
    result = restarted.scan()
    assert result["snapshot"]["id"] != first["snapshot"]["id"]
    assert result["indexing"]["modules_parsed"] == 1
    assert result["indexing"]["modules_resolved"] == 1
    assert result["map_health"]["internal_imports"]["linked"] == 2
    # The resumed map is the same one a full read of the checkout gives.
    fresh = Application(root, tmp_path / "fresh.sqlite")
    fresh.scan()
    assert set(restarted._current().edges) == set(fresh._current().edges)
    assert {node.id for node in restarted._current().nodes} == {
        node.id for node in fresh._current().nodes
    }


def test_adding_and_removing_a_module_relinks_the_files_that_asked_for_its_name(tmp_path):
    root = checkout(tmp_path / "checkout")
    application = Application(root, tmp_path / "knowledge.sqlite")
    (root / "service.py").write_text(
        "from store import save\nfrom audit import record\n\n\ndef create(item):\n"
        "    record(item)\n    return save(item)\n"
    )
    assert application.scan()["map_health"]["internal_imports"]["linked"] == 2
    (root / "audit.py").write_text("def record(item):\n    return item\n")
    indexing = application.scan()
    assert indexing["indexing"]["modules_parsed"] == 1
    assert indexing["indexing"]["modules_resolved"] == 2  # audit.py and the file that asked.
    assert indexing["map_health"]["internal_imports"]["linked"] == 3
    (root / "audit.py").unlink()
    indexing = application.scan()
    assert indexing["indexing"]["modules_resolved"] == 1
    assert indexing["map_health"]["internal_imports"]["linked"] == 2


def test_calls_follow_the_checkout_without_a_scan_and_without_reading_the_map_back(
    tmp_path, monkeypatch
):
    root = checkout(tmp_path / "checkout")
    application = Application(root, tmp_path / "knowledge.sqlite")
    brief = application.prepare_change("Change how an item is saved")
    monkeypatch.setattr(
        application.store, "load_graph", lambda *_args: pytest.fail("The saved map was read back")
    )
    (root / "store.py").write_text("LIMIT = 3\n\n\ndef save(item):\n    return [item]\n")
    assert application.inspect("module:store.py")["snapshot_id"] != brief["snapshot_id"]
    assessed = application.assess_plan("Change how an item is saved", ["store.py"])
    assert assessed["snapshot_id"] == application.indexer.snapshot().id
    (root / "billing.py").write_text("def bill():\n    return 1\n")
    assert application.modules()["total"] == 5
    monkeypatch.undo()
    validated = application.validate_change(baseline_id=brief["baseline_id"])
    assert validated["changed_paths"] == ["billing.py", "store.py"]


def test_a_new_worktree_starts_from_the_facts_another_worktree_saved(tmp_path):
    import subprocess

    def git(root: Path, *arguments: str) -> None:
        subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)

    main = checkout(tmp_path / "main")
    (main / ".gitignore").write_text(".anaxigraph-v2/\n")
    git(main, "init", "-q")
    git(main, "config", "user.email", "fixture@example.test")
    git(main, "config", "user.name", "Fixture")
    git(main, "add", ".")
    git(main, "commit", "-qm", "Initial")
    assert Application(main).scan()["indexing"]["modules_parsed"] == 4

    linked = tmp_path / "feature"
    git(main, "worktree", "add", "-q", str(linked), "-b", "feature")
    (linked / "report.py").write_text("def render():\n    return 'feature report'\n")
    result = Application(linked).scan()
    # Only the file that differs is read; the rest comes from the main checkout's saved facts.
    assert result["indexing"]["modules_parsed"] == 1
    assert result["modules"] == 4
    assert result["map_health"]["internal_imports"]["linked"] == 2
    # The seeded map is the one a full read of the worktree gives.
    assert set(Application(linked)._current().edges) == set(index(linked).edges)
