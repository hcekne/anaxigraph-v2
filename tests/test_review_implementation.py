"""Source-aware workers see implementation bodies without duplicating portable understanding."""

from __future__ import annotations

import json
import shutil

import pytest
from test_pattern_screening import annotate
from test_workflows import answer

from anaxigraph.analysis import build_request
from anaxigraph.analysis.packets import catalogue
from anaxigraph.analysis.portable import input_identity
from anaxigraph.application import Application
from anaxigraph.workflows.engine import WorkflowRunner

SOURCE = "# IMPLEMENTATION_ONLY_CANARY\ndef fanout_impl(items):\n    return list(items)\n"


@pytest.fixture
def mapped(tmp_path):
    (tmp_path / "subject.py").write_text(SOURCE)
    (tmp_path / "other.py").write_text("def other():\n    return 42\n")
    (tmp_path / "excluded.py").write_text("# EXCLUDED_SOURCE_CANARY\n")
    app = Application(tmp_path, excluded=("excluded.py",))
    app.scan()
    annotate(app, ["subject.py"])
    return app


@pytest.mark.parametrize("kind", ["patterns", "pattern-screen", "architecture", "exhaustive"])
def test_actual_requests_deliver_only_scoped_source_and_keep_saved_inputs_compact(mapped, kind):
    options = {"pattern_ids": [catalogue()[0]["id"]]} if kind == "patterns" else {}
    request = mapped.analysis_request(
        "patterns" if kind == "exhaustive" else kind, ["subject.py"], **options
    )
    assert request["implementation_evidence"] == {"subject.py": SOURCE}
    assert (
        request["packet"]["modules"][0]["fingerprint"]
        == mapped._current().snapshot.files["subject.py"]
    )
    saved = mapped.store.get_review(str(mapped.root), request["request_id"], "request")["result"]
    assert "implementation_evidence" not in saved
    assert "IMPLEMENTATION_ONLY_CANARY" not in json.dumps(saved)
    assert "EXCLUDED_SOURCE_CANARY" not in json.dumps(request)
    assert input_identity(request) == input_identity(saved)
    assert (
        mapped._rebuild_request(mapped._current(), request["kind"], saved)["request_id"]
        == request["request_id"]
    )


def test_same_interface_and_brief_do_not_hide_changed_implementation(mapped):
    before = mapped.analysis_request("architecture", ["subject.py"])
    (mapped.root / "subject.py").write_text(SOURCE.replace("list(items)", "sorted(items)"))
    mapped.scan()
    annotate(mapped, ["subject.py"])
    after = mapped.analysis_request("architecture", ["subject.py"])
    assert before["packet"]["modules"][0]["brief"] == after["packet"]["modules"][0]["brief"]
    assert before["implementation_evidence"] != after["implementation_evidence"]
    assert input_identity(before) != input_identity(after)
    with pytest.raises(ValueError, match="stale snapshot"):
        mapped.submit_analysis(before["request_id"], answer(before))


def test_source_counts_against_the_input_budget_before_saving_a_request(mapped):
    source = SOURCE + "# A large implementation comment.\n" * 2000
    (mapped.root / "subject.py").write_text(source)
    mapped.scan()
    annotate(mapped, ["subject.py"])
    graph = mapped._current()
    facts = build_request(graph, "architecture", ["subject.py"])
    maximum = len(json.dumps(facts).encode()) + 1000
    before = mapped.store.reviews(str(mapped.root), include_requests=True)
    with pytest.raises(ValueError, match="input budget"):
        mapped.analysis_request("architecture", ["subject.py"], max_packet_bytes=maximum)
    assert mapped.store.reviews(str(mapped.root), include_requests=True) == before


def test_workflows_deliver_source_and_reuse_portable_results_without_raw_source(mapped, tmp_path):
    seen = set()

    def provider(request, *_args, **_kwargs):
        if request["kind"] in {"patterns", "pattern-screen", "architecture"}:
            assert request["implementation_evidence"] == {"subject.py": SOURCE}
            seen.add(request["kind"])
        return answer(request)

    run = WorkflowRunner(mapped, provider).start("patterns", scope=["subject.py"], background=False)
    assert run["status"] == "completed", run
    assert seen == {"patterns", "pattern-screen", "architecture"}
    record = (mapped.root / "anaxigraph/record.json").read_text()
    assert "IMPLEMENTATION_ONLY_CANARY" not in record
    clone = tmp_path / "clone"
    clone.mkdir()
    for name in ("subject.py", "other.py"):
        shutil.copy2(mapped.root / name, clone / name)
    shutil.copytree(mapped.root / "anaxigraph", clone / "anaxigraph")
    target = Application(clone, excluded=("excluded.py",))

    def forbidden(*_args, **_kwargs):
        pytest.fail("Identical portable review inputs should not call a model again")

    resumed = WorkflowRunner(target, forbidden).start(
        "patterns", scope=["subject.py"], background=False
    )
    assert resumed["status"] == "completed", resumed
    assert resumed["reused_batches"] == resumed["total_batches"]


def test_fresh_eyes_stays_behavior_only_even_when_a_builder_receives_sources(mapped):
    graph = mapped._current()
    supplied = build_request(graph, "fresh-eyes", ["subject.py"], sources={"subject.py": SOURCE})
    normal = build_request(graph, "fresh-eyes", ["subject.py"])
    assert supplied == normal
    public = mapped.analysis_request("fresh-eyes", ["subject.py"])
    assert set(public) == {"request_id", "kind", "packet", "response_schema"}
    assert not any(
        value in json.dumps(public)
        for value in ("IMPLEMENTATION_ONLY_CANARY", "subject.py", "fanout_impl")
    )
