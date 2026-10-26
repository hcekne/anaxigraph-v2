"""Reuse exact scoped analyses across documentation changes, guarding opaque resources."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from test_workflows import answer, app

from anaxigraph.application import Application
from anaxigraph.workflows.engine import WorkflowRunner

CONFIG = {
    "timeout_seconds": None,
    "stages": {
        kind: {"provider": "codex", "model": "fixture", "reasoning_effort": "xhigh"}
        for kind in ("annotate", "patterns", "fresh-eyes")
    },
}
RESOURCES = {
    "data/seed.json": '{"snow":1}',
    "settings.toml": "limit = 3\n",
    "static/app.js": "const limit = 3;\n",
    "static/index.html": "<main>Search</main>\n",
    "static/style.css": "main { color: black; }\n",
    "unparsed.py": "def broken(\n",
}
CHANGED_RESOURCES = {
    "data/seed.json": '{"snow":2}',
    "settings.toml": "limit = 4\n",
    "static/app.js": "const limit = 4;\n",
    "static/index.html": "<main>Explore</main>\n",
    "static/style.css": "main { color: blue; }\n",
    "unparsed.py": "def still_broken(\n",
}


def git(root, *arguments):
    return subprocess.run(
        ["git", "-C", str(root), *arguments], check=True, capture_output=True, text=True
    ).stdout.strip()


def fixture(tmp_path, kind, *, evidence=False, initialize_git=False):
    root = tmp_path / "original"
    root.mkdir()
    application = app(root, count=2)
    for path, content in RESOURCES.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    (root / "POLICY.md").write_text("Preserve accepted input guarantees.\n")
    (root / "review.md").write_text("Initial review notes.\n")
    if initialize_git:
        git(root, "init", "-q")
        git(root, "config", "user.name", "Fixture")
        git(root, "config", "user.email", "fixture@example.test")
        git(root, "add", ".")
        git(root, "commit", "-qm", "Initial source")
    calls = []

    def provider(request, *_args, **_kwargs):
        calls.append(request["kind"])
        result = answer(request)
        if evidence and request["kind"] == "annotate":
            result["annotations"][0]["contracts"] = [
                {
                    "statement": "Preserve accepted input guarantees.",
                    "basis": "declared",
                    "evidence": [{"path": "POLICY.md", "line": 1}],
                }
            ]
        return result

    result = WorkflowRunner(application, provider).start(
        kind, CONFIG, ["unit0.py"], background=False
    )
    assert result["status"] == "completed", result
    return application, provider, calls


def clone(original, tmp_path):
    root = tmp_path / "clone"
    shutil.copytree(original.root, root, ignore=shutil.ignore_patterns(".git", ".anaxigraph-v2"))
    return Application(root)


@pytest.mark.parametrize("kind,review_count", [("patterns", 1), ("fresh-eyes", 2)])
def test_doc_only_commit_and_fresh_clone_reuse_without_calls_and_keep_provenance(
    tmp_path, kind, review_count
):
    original, provider, calls = fixture(tmp_path, kind, initialize_git=True)
    record = original.root / "anaxigraph/record.json"
    git(original.root, "add", "anaxigraph/record.json")
    git(original.root, "commit", "-qm", "Save initial analyses")
    original.scan()
    original.export_record()
    analyses = json.loads(record.read_text())["analyses"]
    assert all(len(row["analysis_context"]) == 64 for row in analyses)
    initial_calls = len(calls)
    (original.root / "review.md").write_text("Record the design choices and executed checks.\n")
    git(original.root, "add", "review.md")
    git(original.root, "commit", "-qm", "Document review decisions")
    imported = original.scan()["portable_import"]
    assert imported["analyses_imported"] == review_count
    original.export_record()
    assert json.loads(record.read_text())["analyses"] == analyses
    git(original.root, "add", "anaxigraph/record.json")
    git(original.root, "commit", "-qm", "Carry current portable source fingerprints")
    before = record.read_bytes()
    original.scan()
    assert original.export_record()["changed"] is False
    assert record.read_bytes() == before
    cloned_root = tmp_path / "fresh-clone"
    subprocess.run(["git", "clone", "-q", str(original.root), str(cloned_root)], check=True)
    cloned = Application(cloned_root)
    assert cloned.scan()["portable_import"]["analyses_imported"] == review_count
    repeated = WorkflowRunner(cloned, provider).start(kind, CONFIG, ["unit0.py"], background=False)
    assert repeated["status"] == "completed", repeated
    assert repeated["reused_batches"] == review_count
    assert len(calls) == initial_calls
    for _ in range(2):
        cloned.scan()
        cloned.export_record()
    assert len(calls) == initial_calls


@pytest.mark.parametrize("kind", ["patterns", "fresh-eyes"])
@pytest.mark.parametrize("path", list(RESOURCES))
def test_changed_opaque_behavioral_resource_blocks_reuse(tmp_path, kind, path):
    original, _provider, calls = fixture(tmp_path, kind)
    target = clone(original, tmp_path)
    (target.root / path).write_text(CHANGED_RESOURCES[path])
    count = len(calls)
    imported = target.scan()["portable_import"]
    assert imported["annotations_imported"] == 1
    assert imported["analyses_imported"] == 0
    assert target.reviews(kind)["total"] == 0
    assert len(calls) == count


@pytest.mark.parametrize("kind,review_count", [("patterns", 1), ("fresh-eyes", 2)])
@pytest.mark.parametrize("path,compatible", [("unit0.py", False), ("unit1.py", True)])
def test_python_changes_are_checked_by_exact_scoped_input(
    tmp_path, kind, review_count, path, compatible
):
    original, provider, calls = fixture(tmp_path, kind)
    target = clone(original, tmp_path)
    source = target.root / path
    source.write_text(source.read_text().replace("return", "return 10 +"))
    count = len(calls)
    imported = target.scan()["portable_import"]
    assert imported["analyses_imported"] == (review_count if compatible else 0)
    if compatible:
        repeated = WorkflowRunner(target, provider).start(
            kind, CONFIG, ["unit0.py"], background=False
        )
        assert repeated["status"] == "completed", repeated
        assert len(calls) == count


@pytest.mark.parametrize("kind", ["patterns", "fresh-eyes"])
def test_cited_document_is_guarded_from_annotation_evidence(tmp_path, kind):
    original, _provider, _calls = fixture(tmp_path, kind, evidence=True)
    target = clone(original, tmp_path)
    (target.root / "POLICY.md").write_text("Changed admission and validation obligations.\n")
    imported = target.scan()["portable_import"]
    assert imported["analyses_imported"] == 0


def test_document_cited_only_outside_the_review_scope_does_not_invalidate_it(tmp_path):
    original, provider, calls = fixture(tmp_path, "patterns")
    request = original.analysis_request("annotate", ["unit1.py"])
    result = answer(request)
    result["annotations"][0]["contracts"] = [
        {
            "statement": "Preserve documented admission guarantees.",
            "basis": "declared",
            "evidence": [{"path": "POLICY.md", "line": 1}],
        }
    ]
    original.submit_analysis(request["request_id"], result)
    original.export_record()
    target = clone(original, tmp_path)
    (target.root / "POLICY.md").write_text("Changed obligations for an unrelated responsibility.\n")
    assert target.scan()["portable_import"]["analyses_imported"] == 1
    count = len(calls)
    repeated = WorkflowRunner(target, provider).start(
        "patterns", CONFIG, ["unit0.py"], background=False
    )
    assert repeated["status"] == "completed", repeated
    assert len(calls) == count


@pytest.mark.parametrize("changed", [False, True])
def test_legacy_context_is_backfilled_only_after_global_match(tmp_path, changed):
    original, _provider, _calls = fixture(tmp_path, "patterns")
    path = original.root / "anaxigraph/record.json"
    record = json.loads(path.read_text())
    for row in record["analyses"]:
        row.pop("analysis_context")
    path.write_text(json.dumps(record))
    target = clone(original, tmp_path)
    if changed:
        (target.root / "review.md").write_text("A later review document.\n")
    imported = target.scan()["portable_import"]
    assert imported["analyses_imported"] == (0 if changed else 1)
    target.export_record()
    rows = json.loads((target.root / "anaxigraph/record.json").read_text())["analyses"]
    if changed:
        assert all(row.get("analysis_context") is None for row in rows)
    else:
        assert all(len(row["analysis_context"]) == 64 for row in rows)
