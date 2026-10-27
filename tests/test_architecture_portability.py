"""Architecture lenses retain independent identities and reusable portable evidence."""

from __future__ import annotations

import shutil
from collections import Counter

from test_workflows import answer

from anaxigraph.application import Application
from anaxigraph.storage.portable import read_record
from anaxigraph.workflows.engine import WorkflowRunner

LENSES = ("boundaries", "packages", "verification")
CONFIG = {
    "timeout_seconds": None,
    "stages": {
        "architecture": {
            "provider": "codex",
            "model": "synthetic-scout",
            "reasoning_effort": "low",
        }
    },
}


def architecture_answer(request):
    return {
        "producer": "synthetic-architecture-portability",
        "lens": request["request_options"]["lens"],
        "assessments": [
            {"module_id": module_id, "outcome": "keep"} for module_id in request["scope"]
        ],
        "findings": [],
        "uncertainty": [],
    }


def fixture(tmp_path):
    root = tmp_path / "original"
    root.mkdir()
    for name in ("first", "second"):
        (root / f"{name}.py").write_text(f"def {name}(value):\n    return value\n")
    application = Application(root)
    result = WorkflowRunner(application, lambda request, *_args, **_kwargs: answer(request)).start(
        "mapping", background=False
    )
    assert result["status"] == "completed", result
    for path in ("first.py", "second.py"):
        for lens in LENSES:
            request = application.analysis_request("architecture", [path], lens=lens)
            application.submit_analysis(request["request_id"], architecture_answer(request))
    application.export_record()
    return application


def cloned(original, tmp_path):
    root = tmp_path / "different" / "clone"
    shutil.copytree(original.root, root, ignore=shutil.ignore_patterns(".git", ".anaxigraph-v2"))
    return Application(root)


def test_each_lens_survives_a_different_checkout_path_and_unchanged_cycles(tmp_path):
    original = fixture(tmp_path)
    source_snapshot = original.status()["snapshot"]["id"]
    original_rows = {
        (tuple(row["result"]["scope"]), row["result"]["request_options"]["lens"]): row
        for row in original.store.reviews(str(original.root), "architecture")
    }
    assert len(original_rows) == 6
    assert (
        len(
            [row for row in read_record(original.root)["analyses"] if row["kind"] == "architecture"]
        )
        == 6
    )
    target = cloned(original, tmp_path)
    imported = target.scan()["portable_import"]
    assert imported["analyses_imported"] == 6
    assert target.status()["annotations"]["current"] == 2
    rows = target.store.reviews(str(target.root), "architecture")
    assert len(rows) == 6
    for row in rows:
        body = row["result"]
        old = original_rows[(tuple(body["scope"]), body["request_options"]["lens"])]
        assert body["result"] == old["result"]["result"]
        assert body["input_identity"] == old["result"]["input_identity"]
        assert body["provenance"]["original_id"] == old["id"]
        assert body["provenance"]["source_snapshot"] == source_snapshot
    assert all(row["status"] == "current" for row in target.reviews("architecture")["items"])
    target.export_record()
    path = target.root / "anaxigraph/record.json"
    before = path.read_bytes(), path.stat().st_mtime_ns
    size = target.store.path.stat().st_size
    for _ in range(3):
        target.scan()
        target.import_record()
        target.export_record()
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before
        assert target.store.path.stat().st_size == size


def test_editing_one_scope_does_not_import_stale_results_or_discard_other_lenses(tmp_path):
    original = fixture(tmp_path)
    target = cloned(original, tmp_path)
    (target.root / "first.py").write_text("def first(value):\n    return value + 1\n")
    imported = target.scan()["portable_import"]
    assert imported["analyses_imported"] == 3
    rows = target.store.reviews(str(target.root), "architecture")
    assert {tuple(row["result"]["scope"]) for row in rows} == {("module:second.py",)}
    assert {row["result"]["request_options"]["lens"] for row in rows} == set(LENSES)
    assert all(row["status"] == "current" for row in target.reviews("architecture")["items"])


def test_current_lenses_reuse_without_provider_calls_and_pipeline_changes_retry(tmp_path):
    application = fixture(tmp_path)
    calls = Counter()

    def provider(request, *_args, **_kwargs):
        calls[request["kind"]] += 1
        return (
            architecture_answer(request) if request["kind"] == "architecture" else answer(request)
        )

    runner = WorkflowRunner(application, provider)
    initial = runner.start("architecture", CONFIG, ["first.py"], background=False)
    assert initial["status"] == "completed", initial
    assert calls == Counter({"architecture": 3})
    calls.clear()
    target = cloned(application, tmp_path)
    target.scan()
    repeated = WorkflowRunner(target, provider).start(
        "architecture", CONFIG, ["first.py"], background=False
    )
    assert repeated["status"] == "completed", repeated
    assert not calls
    changed = {
        **CONFIG,
        "stages": {
            "architecture": {**CONFIG["stages"]["architecture"], "model": "synthetic-other-scout"}
        },
    }
    refreshed = WorkflowRunner(target, provider).start(
        "architecture", changed, ["first.py"], background=False
    )
    assert refreshed["status"] == "completed", refreshed
    assert calls == Counter({"architecture": 3})
