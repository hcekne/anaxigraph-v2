"""Cross-component audit regressions using real source and SQLite publications."""

import json
from pathlib import Path

import pytest

from anaxigraph.application import Application


def application(tmp_path: Path) -> Application:
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "service.py").write_text("def create(value):\n    return value\n")
    (root / "storage.py").write_text("def persist(value):\n    return value\n")
    app = Application(root, tmp_path / "knowledge.sqlite")
    app.scan()
    return app


def annotation_result(request: dict) -> dict:
    return {
        "producer": "audit-fixture",
        "annotations": [
            {
                "node_id": node["id"],
                "overview": "Keep the invoice operation available.",
                "detail": "Handle one invoice operation through a small callable interface.",
                "capabilities": ["Accept and preserve invoice information."],
                "functions": [
                    {"node_id": item["id"], "overview": "Perform the invoice operation."}
                    for item in request["packet"]["functions"]
                    if item["path"] == node["path"]
                ],
                "contracts": [
                    {
                        "statement": "Preserve supplied invoice information.",
                        "basis": "inferred",
                        "evidence": [{"path": node["path"], "line": 1}],
                    }
                ],
                "uncertainty": ["Runtime failure handling is untested."],
            }
            for node in request["packet"]["modules"]
        ],
    }


def test_annotation_submit_patches_cached_modules_without_reloading_the_graph(
    tmp_path, monkeypatch
):
    app = application(tmp_path)
    request = app.analysis_request("annotate", ["service.py", "storage.py"])
    app._current()

    def no_reload(*args, **kwargs):
        pytest.fail("Own annotation writes must not reload the whole source graph")

    monkeypatch.setattr(app.store, "load_graph", no_reload)
    result = annotation_result(request)
    app.submit_analysis(request["request_id"], result)
    assert all(
        node.data.get("annotation_status") == "current"
        for node in app._current().nodes
        if node.kind == "module"
    )
    app.submit_analysis(request["request_id"], result)
    assert app.inspect("service.py")["entity"]["data"]["annotation_status"] == "current"


def test_annotation_cache_patch_does_not_hide_another_process_publication(tmp_path, monkeypatch):
    app = application(tmp_path)
    other = Application(app.root, app.store.path)
    request = app.analysis_request("annotate", ["service.py"])
    annotate = app.store.annotate

    def interleaved(*args, **kwargs):
        changed = annotate(*args, **kwargs)
        other.store.annotate(
            str(app.root),
            request["snapshot_id"],
            "module:storage.py",
            {"overview": "Concurrent understanding must stay visible."},
        )
        return changed

    monkeypatch.setattr(app.store, "annotate", interleaved)
    app.submit_analysis(request["request_id"], annotation_result(request))
    annotation = app.inspect("storage.py")["entity"]["data"]["annotation"]
    assert annotation["overview"] == "Concurrent understanding must stay visible."


def test_change_preparation_rejects_unscanned_source_before_pinning_baseline(tmp_path):
    app = application(tmp_path)
    (app.root / "service.py").write_text("def create(value):\n    return value + 1\n")
    with pytest.raises(ValueError, match="Checkout changed"):
        app.prepare_change("Update invoice creation")
    assert app.store.stats(str(app.root))["pinned_snapshot_id"] is None


def test_annotation_request_handles_a_real_module_larger_than100kb(tmp_path):
    app = application(tmp_path)
    source = "#" + "x" * 150_000 + "\ndef create(value):\n    return value\n"
    (app.root / "service.py").write_text(source)
    app.scan()
    request = app.analysis_request("annotate", ["service.py"])
    assert request["packet"]["source"]["service.py"] == source


def test_portable_export_does_not_cache_a_concurrently_superseded_record(tmp_path, monkeypatch):
    import anaxigraph.application.records as application_module

    app = application(tmp_path)
    first = app.analysis_request("annotate", ["service.py"])
    app.submit_analysis(first["request_id"], annotation_result(first))
    other = Application(app.root, app.store.path)
    concurrent = other.analysis_request("annotate", ["storage.py"])
    publish = application_module.write_record
    interleaved = []

    def publish_after_another_annotation(*args, **kwargs):
        if not interleaved:
            interleaved.append(True)
            other.submit_analysis(concurrent["request_id"], annotation_result(concurrent))
        return publish(*args, **kwargs)

    monkeypatch.setattr(application_module, "write_record", publish_after_another_annotation)
    app.export_record()
    app.export_record()
    record = json.loads((app.root / "anaxigraph" / "record.json").read_text())
    assert {module["id"] for module in record["modules"]} == {
        "module:service.py",
        "module:storage.py",
    }
