"""Current default shortlists replace one module shard; explicit reviews survive."""

from __future__ import annotations

import copy

import pytest
from test_portable_context import git
from test_workflows import answer, app

from anaxigraph.analysis.portable import input_identity
from anaxigraph.application import Application
from anaxigraph.application import reviews as review_api
from anaxigraph.storage.portable import read_record, write_record
from anaxigraph.workflows.engine import WorkflowRunner


def test_default_shortlist_churn_replaces_sql_and_portable_rows_but_keeps_explicit_reviews(
    tmp_path,
):
    root = tmp_path / "original"
    root.mkdir()
    application = app(root, count=1)
    calls, selected = [], []

    def provider(request, *_args, **_kwargs):
        calls.append(request["kind"])
        result = answer(request)
        if request["kind"] == "pattern-screen":
            result["selections"][0]["pattern_ids"] = list(selected)
        return result

    runner = WorkflowRunner(application, provider)
    assert runner.start("mapping", background=False)["status"] == "completed"
    catalogue = application.analysis_request("pattern-screen")["packet"]["catalogue"]
    explicit = []
    for cards in ([catalogue[-1]["id"]], [catalogue[-2]["id"]], None):
        options = {"pattern_ids": cards} if cards is not None else {}
        request = application.analysis_request("patterns", ["unit0.py"], **options)
        application.submit_analysis(request["request_id"], answer(request))
        explicit.append(application.store.get_review(str(root), request["request_id"], "patterns"))
    saved_id = None
    for phase in range(3):
        selected[:] = [catalogue[phase]["id"]]
        (root / "unit0.py").write_text(f"def operation_0():\n    return {phase + 1}\n")
        result = runner.start("patterns", background=False)
        assert result["status"] == "completed", result
        assert result["reconciliation"]["selected_pairs"] == 1
        rows = application.store.reviews(str(root), "patterns")
        default = [row for row in rows if row["result"].get("pattern_mode") == "screened"]
        assert len(rows) == 4 and len(default) == 1
        body = default[0]["result"]
        assert body["request_options"]["pattern_ids"] == selected
        assert [item["pattern_id"] for item in body["result"]["evaluations"]] == selected
        request = application._rebuild_request(application._current(), "patterns", body)
        assert input_identity(request) == body["input_identity"]
        assert request["request_id"] == default[0]["id"]
        assert body["result"]["execution"]["pipeline"]["worker"]["reasoning_effort"] == "medium"
        record = read_record(root)
        portable = [row for row in record["analyses"] if row["kind"] == "patterns"]
        current = [
            row
            for row in portable
            if row["body"].get("pattern_mode") == "screened" and not row.get("explicitly_saved")
        ]
        assert (
            len(current) == 1 and current[0]["body"]["request_options"]["pattern_ids"] == selected
        )
        if phase == 0:
            saved = copy.deepcopy(current[0]) | {"explicitly_saved": True}
            saved_id = saved["id"]
            record["analyses"].append(saved)
            write_record(root, record)
        else:
            assert len(portable) == 5
            assert next(row for row in portable if row.get("explicitly_saved"))["id"] == saved_id
        for original in explicit:
            retained = next(row for row in rows if row["id"] == original["id"])
            assert retained["result"] == original["result"]
            assert (
                next(row for row in portable if row["id"] == original["id"])["body"]
                == original["result"]
            )
    before = (root / "anaxigraph/record.json").read_bytes()
    size = application.store.path.stat().st_size
    calls.clear()
    for _ in range(3):
        result = runner.start("patterns", background=False)
        assert result["status"] == "completed", result
        assert (root / "anaxigraph/record.json").read_bytes() == before
        assert application.store.path.stat().st_size == size
    assert calls == []


def test_legacy_cache_tag_upgrade_keeps_ids_meaning_pipeline_and_clone_provenance(
    tmp_path, monkeypatch
):
    root = tmp_path / "original"
    root.mkdir()
    application = app(root, count=1)
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.test")
    git(root, "add", "unit0.py")
    git(root, "commit", "-qm", "Initial source")
    calls = []

    def provider(request, *_args, **_kwargs):
        calls.append(request["kind"])
        return answer(request)

    original_metadata = review_api._screened_metadata

    def legacy_metadata(kind, options):
        options.pop("screened", None)
        return {}

    monkeypatch.setattr(review_api, "_screened_metadata", legacy_metadata)
    runner = WorkflowRunner(application, provider)
    first = runner.start("patterns", background=False)
    assert first["status"] == "completed", first
    old = next(row for row in read_record(root)["analyses"] if row["kind"] == "patterns")
    assert "pattern_mode" not in old["body"]
    monkeypatch.setattr(review_api, "_screened_metadata", original_metadata)
    calls.clear()
    again = runner.start("patterns", background=False)
    assert again["status"] == "completed", again
    assert calls == []
    record = read_record(root)
    detail = [row for row in record["analyses"] if row["kind"] == "patterns"]
    assert len(detail) == 1
    assert detail[0] == old | {"body": old["body"] | {"pattern_mode": "screened"}}
    body = detail[0]["body"]
    cards = body["request_options"]["pattern_ids"]
    manual = application.analysis_request("patterns", ["unit0.py"], pattern_ids=cards)
    tagged = application.analysis_request(
        "patterns", ["unit0.py"], pattern_ids=cards, screened=True
    )
    assert manual["request_id"] == tagged["request_id"] == old["id"]
    assert input_identity(manual) == input_identity(tagged) == body["input_identity"]
    assert all(
        manual[key] == tagged[key] for key in ("packet", "response_schema", "request_options")
    )
    git(root, "add", "anaxigraph/record.json")
    git(root, "commit", "-qm", "Save portable current analyses")
    application.scan()
    rebound = runner.start("patterns", background=False)
    assert rebound["status"] == "completed", rebound
    assert calls == []
    clone_root = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--no-local", str(root), str(clone_root))
    clone = Application(clone_root)
    clone.scan()
    restored = WorkflowRunner(clone, provider).start("patterns", background=False)
    assert restored["status"] == "completed", restored
    assert calls == []
    row = clone.store.reviews(str(clone_root), "patterns")[0]
    assert row["result"]["pattern_mode"] == "screened"
    assert row["result"]["provenance"]["original_id"] == old["id"]
    assert row["result"]["result"] == old["body"]["result"]
    clone.export_record()
    before = (clone_root / "anaxigraph/record.json").read_bytes()
    clone.export_record()
    assert (clone_root / "anaxigraph/record.json").read_bytes() == before


@pytest.mark.parametrize("replace_pipeline", [False, True])
def test_same_id_manual_selected_intent_survives_default_cache_or_provider_then_churn(
    tmp_path, replace_pipeline
):
    application = app(tmp_path, count=1)
    calls, phase = [], 0

    def provider(request, *_args, **_kwargs):
        calls.append(request["kind"])
        result = answer(request)
        if phase and request["kind"] == "pattern-screen":
            result["selections"][0]["pattern_ids"] = [request["packet"]["catalogue"][2]["id"]]
        return result

    runner = WorkflowRunner(application, provider)
    assert runner.start("mapping", background=False)["status"] == "completed"
    cards = [
        card["id"]
        for card in application.analysis_request("pattern-screen")["packet"]["catalogue"][:2]
    ]
    manual = application.analysis_request("patterns", ["unit0.py"], pattern_ids=cards)
    result = answer(manual)
    worker = copy.deepcopy(runner.configuration()["stages"]["patterns"])
    if replace_pipeline:
        worker["model"] = "previous-explicit-model"
    actual = {key: value for key, value in worker.items() if key != "fallbacks"}
    result["execution"] = {
        "pipeline": {"worker": worker, "review_passes": []},
        "worker": actual,
        "review_passes": [],
    }
    application.submit_analysis(manual["request_id"], result)
    calls.clear()
    default = runner.start("patterns", background=False)
    assert default["status"] == "completed", default
    assert calls.count("patterns") == int(replace_pipeline)
    retained = application.store.get_review(str(tmp_path), manual["request_id"], "patterns")
    assert retained["result"]["pattern_mode"] == "selected"
    assert retained["result"]["request_options"]["pattern_ids"] == cards
    assert retained["result"]["result"]["execution"]["pipeline"]["worker"]["model"] == "gpt-6.1-sol"
    phase = 1
    (tmp_path / "unit0.py").write_text("def operation_0():\n    return 9\n")
    changed = runner.start("patterns", background=False)
    assert changed["status"] == "completed", changed
    rows = application.store.reviews(str(tmp_path), "patterns")
    assert len(rows) == 2
    assert (
        next(row for row in rows if row["id"] == manual["request_id"])["result"]
        == retained["result"]
    )
    portable = [row for row in read_record(tmp_path)["analyses"] if row["kind"] == "patterns"]
    assert {row["body"]["pattern_mode"] for row in portable} == {"selected", "screened"}


def test_compatible_explicit_shard_reuses_after_snapshot_change_with_different_default_shortlist(
    tmp_path,
):
    application = app(tmp_path, count=1)
    runner = WorkflowRunner(application, lambda request, *_args, **_kwargs: answer(request))
    assert runner.start("patterns", background=False)["status"] == "completed"
    catalogue = application.analysis_request("pattern-screen")["packet"]["catalogue"]
    cards = [catalogue[-1]["id"]]
    manual = application.analysis_request("patterns", ["unit0.py"], pattern_ids=cards)
    application.submit_analysis(manual["request_id"], answer(manual))
    (tmp_path / "anaxigraph/record.json").unlink()
    (tmp_path / "unrelated.py").write_text("value = 1\n")
    application.scan()
    current = application.analysis_request(
        "patterns", ["unit0.py"], pattern_ids=cards, screened=True
    )
    assert current["request_id"] != manual["request_id"]
    assert input_identity(current) == input_identity(manual)
    assert application.analysis_saved(current["request_id"])
    restored = application.store.get_review(str(tmp_path), current["request_id"], "patterns")
    assert restored["result"]["pattern_mode"] == "selected"
    assert restored["result"]["provenance"]["original_id"] == manual["request_id"]
    (tmp_path / "unit0.py").write_text("def operation_0():\n    return 3\n")
    application.scan()
    assert runner.start("mapping", background=False)["status"] == "completed"
    changed = application.analysis_request(
        "patterns", ["unit0.py"], pattern_ids=cards, screened=True
    )
    assert not application.analysis_saved(changed["request_id"])


def test_default_portable_import_cannot_remove_same_id_known_manual_intent(tmp_path):
    application = app(tmp_path, count=1)
    runner = WorkflowRunner(application, lambda request, *_args, **_kwargs: answer(request))
    assert runner.start("mapping", background=False)["status"] == "completed"
    manual = application.analysis_request("patterns", ["unit0.py"], pattern_ids=["adapter"])
    application.submit_analysis(manual["request_id"], answer(manual))
    application.export_record()
    record = read_record(tmp_path)
    row = next(row for row in record["analyses"] if row["kind"] == "patterns")
    row["body"]["pattern_mode"] = "screened"
    write_record(tmp_path, record)
    application.import_record()
    retained = application.store.get_review(str(tmp_path), manual["request_id"], "patterns")
    assert retained["result"]["pattern_mode"] == "selected"
    request = application.store.get_review(str(tmp_path), manual["request_id"], "request")
    assert request["result"]["pattern_mode"] == "selected"


def test_explicitly_reusing_legacy_or_default_result_saves_manual_intent_portably(tmp_path):
    application = app(tmp_path, count=1)
    runner = WorkflowRunner(application, lambda request, *_args, **_kwargs: answer(request))
    assert runner.start("patterns", background=False)["status"] == "completed"
    original = read_record(tmp_path)
    detail = next(row for row in original["analyses"] if row["kind"] == "patterns")
    cards = detail["body"]["request_options"]["pattern_ids"]
    manual = application.analysis_request("patterns", ["unit0.py"], pattern_ids=cards)
    assert manual["request_id"] == detail["id"]
    assert application.analysis_saved(manual["request_id"])
    application.export_record()
    rows = [row for row in read_record(tmp_path)["analyses"] if row["kind"] == "patterns"]
    assert len(rows) == 1 and rows[0]["body"]["pattern_mode"] == "selected"
    assert rows[0]["id"] == detail["id"] and rows[0]["body"]["result"] == detail["body"]["result"]
    # Emulate an old, identical portable row without a recorded retention intent.
    record = read_record(tmp_path)
    next(row for row in record["analyses"] if row["kind"] == "patterns")["body"].pop("pattern_mode")
    write_record(tmp_path, record)
    application.export_record()
    rows = [row for row in read_record(tmp_path)["analyses"] if row["kind"] == "patterns"]
    assert len(rows) == 1 and rows[0]["body"]["pattern_mode"] == "selected"


@pytest.mark.parametrize(
    "kind,options",
    [
        ("patterns", {"screened": 1, "pattern_ids": []}),
        ("patterns", {"screened": True}),
        ("pattern-screen", {"screened": True, "pattern_ids": []}),
    ],
)
def test_retention_tag_rejects_wrong_analysis_or_non_boolean_options(tmp_path, kind, options):
    application = app(tmp_path, count=1)
    with pytest.raises(ValueError, match="screened"):
        application.analysis_request(kind, ["unit0.py"], **options)
