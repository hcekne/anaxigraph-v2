"""Actual cross-checkout records and atomic publication, without model calls."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from anaxigraph.application import Application
from anaxigraph.storage.portable import read_record, write_record


def annotate_module(app: Application) -> None:
    request = app.analysis_request("annotate", ["billing.py"])
    result = {
        "producer": "portable-fixture",
        "annotations": [
            {
                "node_id": request["scope"][0],
                "overview": "Prepare tenant invoices.",
                "detail": "Validate invoice amounts while preserving tenant ownership.",
                "capabilities": ["Prepare an invoice for its authorized tenant."],
                "functions": [
                    {"node_id": node["id"], "overview": "Prepare an invoice amount."}
                    for node in request["packet"]["functions"]
                ],
                "contracts": [
                    {
                        "statement": "Preserve tenant isolation.",
                        "basis": "inferred",
                        "evidence": [{"path": "billing.py", "line": 1}],
                    }
                ],
                "uncertainty": [],
            }
        ],
    }
    app.submit_analysis(request["request_id"], result)


def fixture_app(tmp_path: Path) -> Application:
    root = tmp_path / "original"
    root.mkdir()
    (root / "billing.py").write_text("def amount(value):\n    return round(value, 2)\n")
    app = Application(root)
    app.scan()
    annotate_module(app)
    return app


def clone(app: Application, tmp_path: Path) -> Application:
    root = tmp_path / "different" / "clone"
    root.mkdir(parents=True)
    shutil.copy2(app.root / "billing.py", root / "billing.py")
    shutil.copytree(app.root / "anaxigraph", root / "anaxigraph")
    return Application(root)


def test_new_path_reuses_compatible_annotations_and_saved_reviews(tmp_path):
    original = fixture_app(tmp_path)
    request = original.analysis_request("patterns", ["billing.py"])
    result = {
        "producer": "portable-fixture",
        "evaluations": [
            {
                "module_id": request["scope"][0],
                "pattern_id": card["id"],
                "opportunity": None,
                "pros": ["Possible independent responsibility."],
                "cons": ["Insufficient evidence."],
                "alternative": "Keep the behavior local.",
                "evidence": [],
            }
            for card in request["packet"]["catalogue"]
        ],
    }
    original.submit_analysis(request["request_id"], result)
    exported = original.export_record()
    assert exported["files"] == 1 and exported["committed_pushed"] is False
    target = clone(original, tmp_path)
    target.scan()  # Automatically read the Git-carried record after local indexing.
    assert target.status()["annotations"]["current"] == 1
    assert original.status()["snapshot"]["id"] != target.status()["snapshot"]["id"]
    reviews = target.reviews("patterns")
    assert reviews["total"] == 1
    saved = target.inspect("review:" + reviews["items"][0]["id"])["entity"]["data"]
    assert saved["result"]["result"] == result
    assert saved["status"] == "current"
    annotation = target.inspect("module:billing.py")["entity"]["data"]["annotation"]
    assert annotation["provenance"]["source_snapshot"] == original.status()["snapshot"]["id"]
    assert original.export_record()["bytes"] < 100_000


def test_changed_source_is_not_restored_as_current_understanding(tmp_path):
    original = fixture_app(tmp_path)
    original.export_record()
    target = clone(original, tmp_path)
    (target.root / "billing.py").write_text("def amount(value):\n    return value\n")
    target.scan()
    assert target.status()["annotations"]["current"] == 0
    assert target.import_record()["annotations_imported"] == 0


def test_unchanged_export_does_not_grow_or_rewrite_record(tmp_path):
    app = fixture_app(tmp_path)
    app.export_record()
    path = app.root / "anaxigraph" / "record.json"
    before = path.read_bytes(), path.stat().st_mtime_ns
    for _ in range(3):
        app.scan()
        app.import_record()
        assert app.export_record()["changed"] is False
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert list(path.parent.iterdir()) == [path]
    assert app.status()["storage"]["snapshots"] <= 3


def test_capacity_and_io_failures_preserve_last_record_and_remove_temporaries(
    tmp_path, monkeypatch
):
    app = fixture_app(tmp_path)
    app.export_record()
    path = app.root / "anaxigraph" / "record.json"
    before = path.read_bytes()
    record = read_record(app.root)
    with pytest.raises(ValueError, match="budget"):
        write_record(app.root, record, maximum=10)
    record["source"]["revision"] = "new-candidate"

    def disk_error(*args, **kwargs):
        raise OSError("simulated publication failure")

    monkeypatch.setattr("anaxigraph.storage.portable.os.replace", disk_error)
    with pytest.raises(OSError, match="publication failure"):
        write_record(app.root, record)
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_symlinked_portable_folder_is_rejected(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "anaxigraph").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        read_record(root)


def test_corrupt_source_identity_does_not_modify_local_understanding(tmp_path):
    app = fixture_app(tmp_path)
    app.export_record()
    path = app.root / "anaxigraph" / "record.json"
    record = json.loads(path.read_text())
    record["source"]["files"]["billing.py"] = "wrong"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="identity"):
        app.import_record()
    assert app.status()["annotations"]["current"] == 1


def test_hierarchical_blind_reviews_survive_cross_checkout_import(tmp_path):
    from test_workflows import answer

    from anaxigraph.workflows.engine import WorkflowRunner

    original = fixture_app(tmp_path)
    runner = WorkflowRunner(original, lambda request, *_args, **_kwargs: answer(request))
    run = runner.start("fresh-eyes", background=False)
    assert run["status"] == "completed", run
    target = clone(original, tmp_path)
    target.scan()
    saved = target.reviews("fresh-eyes")
    assert saved["total"] == 2
    assert all(row["status"] == "current" for row in saved["items"])
    final = target.analysis_reconcile("fresh-eyes", [row["id"] for row in saved["items"]])
    assert final["alternatives"] == 3
    before = (target.root / "anaxigraph" / "record.json").read_bytes()
    target.import_record()
    target.export_record()
    assert (target.root / "anaxigraph" / "record.json").read_bytes() == before
