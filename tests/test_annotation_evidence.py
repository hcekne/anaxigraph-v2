"""Cited contracts remain tied to the evidence that originally supported them."""

import json
import shutil

import pytest
from test_pattern_decisions import make_application
from test_review_applicability import saved_review
from test_workflows import answer

from anaxigraph.application import Application, context_fingerprints


def annotated(root, evidence="POLICY.md"):
    root.mkdir(exist_ok=True)
    (root / "POLICY.md").write_text("Preserve caller-visible guarantees.\n")
    (root / "settings.json").write_text('{"require_validation":true}\n')
    app = make_application(root)
    request = app.analysis_request("annotate", ["delivery.py"])
    result = answer(request)
    result["annotations"][0]["contracts"] = [
        {
            "statement": "Preserve caller-visible guarantees.",
            "basis": "declared",
            "evidence": [{"path": evidence, "line": 1}],
        }
    ]
    app.submit_analysis(request["request_id"], result)
    return app


def change(root, path, edit):
    target = root / path
    if edit == "remove":
        target.unlink()
    elif path.endswith(".py"):
        target.write_text("def lookup(value):\n    return str(value)\n")
    elif path.endswith(".json"):
        target.write_text('{"require_validation":false}\n')
    else:
        target.write_text("The caller-visible obligation has changed.\n")


@pytest.mark.parametrize("evidence", ["POLICY.md", "unrelated.py", "settings.json"])
@pytest.mark.parametrize("edit", ["modify", "remove"])
def test_changed_citation_marks_local_understanding_stale_and_preserves_its_capture(
    tmp_path, evidence, edit
):
    app = annotated(tmp_path, evidence)
    before = app.inspect("module:delivery.py", full=True)["entity"]["data"]
    assert before["annotation_status"] == "current"
    change(tmp_path, evidence, edit)
    app.prepare_change("Improve delivery")
    after = app.inspect("module:delivery.py", full=True)["entity"]["data"]
    assert after["annotation_status"] == "stale"
    assert after["annotation"] == before["annotation"]
    assert after["annotation_stale_reason"]
    assert app.inspect("module:delivery.py")["entity"]["data"]["annotation_status"] == "stale"


@pytest.mark.parametrize("evidence", ["POLICY.md", "unrelated.py", "settings.json"])
@pytest.mark.parametrize("edit", ["modify", "remove"])
def test_portable_import_rejects_changed_citations_without_discarding_saved_understanding(
    tmp_path, evidence, edit
):
    original = annotated(tmp_path / "original", evidence)
    original.export_record()
    clone = tmp_path / "clone"
    shutil.copytree(original.root, clone, ignore=shutil.ignore_patterns(".anaxigraph-v2"))
    change(clone, evidence, edit)
    record = (clone / "anaxigraph/record.json").read_bytes()
    target = Application(clone)
    imported = target.scan()["portable_import"]
    assert (
        target.inspect("module:delivery.py")["entity"]["data"].get("annotation_status", "missing")
        == "missing"
    )
    assert any(row["id"] == "module:delivery.py" for row in imported["skipped"])
    assert (clone / "anaxigraph/record.json").read_bytes() == record


@pytest.mark.parametrize("evidence", ["POLICY.md", "unrelated.py", "settings.json", "delivery.py"])
def test_unchanged_citations_and_unrelated_documents_reuse_understanding_in_a_clone(
    tmp_path, evidence
):
    original = annotated(tmp_path / "original", evidence)
    original.export_record()
    clone = tmp_path / "clone"
    shutil.copytree(original.root, clone, ignore=shutil.ignore_patterns(".anaxigraph-v2"))
    (clone / "README.md").write_text("Unrelated developer notes.\n")
    target = Application(clone)
    imported = target.scan()["portable_import"]
    assert imported["annotations_imported"] == 2
    assert target.inspect("module:delivery.py")["entity"]["data"]["annotation_status"] == "current"
    assert target.inspect("module:unrelated.py")["entity"]["data"]["annotation_status"] == "current"


@pytest.mark.parametrize("evidence", ["POLICY.md", "delivery.py"])
def test_legacy_annotation_requires_proof_for_external_citations_but_keeps_its_original_record(
    tmp_path, evidence
):
    app = annotated(tmp_path, evidence)
    graph = app._current()
    node = next(node for node in graph.nodes if node.id == "module:delivery.py")
    annotation = node.data["annotation"] | {
        "context_fingerprint": context_fingerprints(graph)["delivery.py"]
    }
    app.store.annotate(str(app.root), graph.snapshot.id, node.id, annotation)
    data = Application(tmp_path).inspect(node.id, full=True)["entity"]["data"]
    assert data["annotation_status"] == ("current" if evidence == "delivery.py" else "stale")
    assert data["annotation"] == annotation


@pytest.mark.parametrize("kind", ["annotate", "patterns", "architecture", "fresh-eyes"])
def test_saved_workflow_availability_rejects_unscanned_checkout_changes(tmp_path, kind):
    app = annotated(tmp_path)
    request = (
        app.analysis_request(kind, ["delivery.py"])
        if kind == "annotate"
        else saved_review(app, kind)
    )
    assert app.analysis_saved(request["request_id"])
    original = json.dumps(app.store.get_review(str(tmp_path), request["request_id"], "request"))
    (tmp_path / "delivery.py").write_text("def deliver(items):\n    return sorted(items)\n")
    with pytest.raises(ValueError, match="Checkout changed"):
        app.analysis_saved(request["request_id"])
    assert (
        json.dumps(app.store.get_review(str(tmp_path), request["request_id"], "request"))
        == original
    )


@pytest.mark.parametrize(
    "contracts",
    [None, [None], [{"evidence": None}], [{"evidence": [{}]}], [{"evidence": [{"path": []}]}]],
)
def test_malformed_portable_citations_do_not_prevent_structural_mapping(tmp_path, contracts):
    original = annotated(tmp_path / "original")
    original.export_record()
    clone = tmp_path / "clone"
    shutil.copytree(original.root, clone, ignore=shutil.ignore_patterns(".anaxigraph-v2"))
    path = clone / "anaxigraph/record.json"
    record = json.loads(path.read_text())
    module = next(item for item in record["modules"] if item["id"] == "module:delivery.py")
    module["annotation"]["contracts"] = contracts
    path.write_text(json.dumps(record))
    saved = path.read_bytes()
    target = Application(clone)
    scan = target.scan()
    assert scan["modules"] == 2
    assert scan["mapped_locally"]
    assert scan["portable_import"]["compatible"] is False
    assert path.read_bytes() == saved
