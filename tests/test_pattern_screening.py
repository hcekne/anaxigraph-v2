"""Two-pass selection saves work without claiming unselected patterns are disproven."""

from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest
from test_application import pattern_result

from anaxigraph.analysis import build_request, canonical_result, plan_scopes
from anaxigraph.analysis.portable import input_identity
from anaxigraph.application import Application


def annotate(application, scope=None):
    scopes = [scope] if scope else plan_scopes(application._current(), "annotate")
    for selected in scopes:
        request = application.analysis_request("annotate", selected)
        application.submit_analysis(
            request["request_id"],
            {
                "producer": "synthetic-screening-fixture",
                "annotations": [
                    {
                        "node_id": module["id"],
                        "overview": "Delivers an observable result to the caller.",
                        "detail": "Keeps one small responsibility available through a clear interface.",
                        "capabilities": []
                        if module["data"]["inert_module"]
                        else ["Deliver an observable result to the caller."],
                        "functions": [
                            {"node_id": fn["id"], "overview": "Return the requested result."}
                            for fn in request["packet"]["functions"]
                            if fn["path"] == module["path"]
                        ],
                        "contracts": [],
                        "uncertainty": [],
                    }
                    for module in request["packet"]["modules"]
                ],
            },
        )


@pytest.fixture
def mapped(tmp_path):
    (tmp_path / "app.py").write_text(
        "from worker import compute\ndef run():\n    return compute()\n"
    )
    (tmp_path / "worker.py").write_text("def compute():\n    return 1\n")
    (tmp_path / "marker.py").write_text("pass\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_app.py").write_text(
        "from app import run\ndef test_run():\n    assert run()\n"
    )
    (tmp_path / "package").mkdir()
    (tmp_path / "package/__init__.py").write_text("from worker import compute\n")
    application = Application(tmp_path)
    application.scan()
    annotate(application)
    return application


def selection_result(request, count=3):
    cards = [card["id"] for card in request["packet"]["catalogue"]][:count]
    return {
        "producer": "synthetic-screening-fixture",
        "selections": [
            {"module_id": module["id"], "pattern_ids": cards if index == 0 else []}
            for index, module in enumerate(request["packet"]["modules"])
        ],
    }


def test_default_screen_is_complete_compact_production_only_and_keeps_function_patterns(mapped):
    request = mapped.analysis_request("pattern-screen")
    packet = request["packet"]
    assert set(request["scope"]) == {
        "module:app.py",
        "module:worker.py",
        "module:package/__init__.py",
    }
    assert len(packet["catalogue"]) == 133
    assert {"adapter", "singleton"} <= {card["id"] for card in packet["catalogue"]}
    assert all(set(card) == {"id", "name", "intent", "questions"} for card in packet["catalogue"])
    assert len(packet["charter"]) == 10
    assert "without classes" in packet["instruction"]
    semantic = json.dumps(packet["modules"])
    assert all(
        f'"{key}":' not in semantic
        for key in ("provenance", "execution", "source_snapshot", "request_id")
    )
    assert all("functions" not in module["brief"] for module in packet["modules"])
    assert plan_scopes(mapped._current(), "pattern-screen") == [
        [identity] for identity in request["scope"]
    ]


@pytest.mark.parametrize("count", [0, 1, 3, 10])
def test_screen_ids_round_trip_without_a_forced_minimum_or_prose(mapped, count):
    request = mapped.analysis_request("pattern-screen")
    result = selection_result(request, count)
    result["explanation"] = "Do not preserve generated screening essays."
    result["selections"][0]["reason"] = "Do not preserve per-module prose."
    canonical = canonical_result(request, result)
    assert "explanation" not in canonical and "reason" not in canonical["selections"][0]
    mapped.submit_analysis(request["request_id"], result)
    selections = mapped.analysis_selection(request["request_id"])
    assert sum(len(item["pattern_ids"]) for item in selections) == count
    assert mapped.analysis_saved(request["request_id"])
    selections[0]["pattern_ids"].append("modified-by-caller")
    assert (
        "modified-by-caller"
        not in mapped.analysis_selection(request["request_id"])[0]["pattern_ids"]
    )


@pytest.mark.parametrize("ids", [["unknown"], ["adapter", "adapter"], [None], list(range(11))])
def test_screen_rejects_unknown_duplicate_or_non_text_ids(mapped, ids):
    request = mapped.analysis_request("pattern-screen")
    result = selection_result(request)
    result["selections"][0]["pattern_ids"] = ids
    with pytest.raises(ValueError, match="catalogue IDs"):
        mapped.submit_analysis(request["request_id"], result)


def test_screen_requires_exact_modules_and_at_most_ten_distinct_cards(mapped):
    request = mapped.analysis_request("pattern-screen")
    result = selection_result(request)
    result["selections"][0]["pattern_ids"] = [
        card["id"] for card in request["packet"]["catalogue"]
    ][:11]
    with pytest.raises(ValueError, match="zero to ten"):
        mapped.submit_analysis(request["request_id"], result)
    result = selection_result(request)
    result["selections"][-1] = result["selections"][0]
    with pytest.raises(ValueError, match="duplicate"):
        mapped.submit_analysis(request["request_id"], result)


def test_selected_detail_reconciliation_requires_only_selected_pairs_and_reports_exclusions(mapped):
    screen = mapped.analysis_request("pattern-screen")
    selected = selection_result(screen)
    mapped.submit_analysis(screen["request_id"], selected)
    with pytest.raises(ValueError, match="detailed coverage"):
        mapped.analysis_reconcile("patterns", [screen["request_id"]])
    first = selected["selections"][0]
    detail = mapped.analysis_request(
        "patterns", [first["module_id"]], pattern_ids=first["pattern_ids"]
    )
    mapped.submit_analysis(detail["request_id"], pattern_result(detail))
    reconciled = mapped.analysis_reconcile("patterns", [screen["request_id"], detail["request_id"]])
    assert reconciled["mode"] == "screened"
    assert reconciled["screened_pairs"] == 399
    assert reconciled["selected_pairs"] == reconciled["detailed_pairs"] == 3
    assert {item["path"] for item in reconciled["excluded_modules"]} == {
        "marker.py",
        "tests/test_app.py",
    }
    assert "not proven" in reconciled["screening_note"]
    with pytest.raises(ValueError, match="duplicate detailed"):
        mapped.analysis_reconcile(
            "patterns", [screen["request_id"], detail["request_id"], detail["request_id"]]
        )


def test_zero_selection_needs_no_detail_and_empty_detail_is_valid(mapped):
    request = mapped.analysis_request("pattern-screen")
    mapped.submit_analysis(request["request_id"], selection_result(request, 0))
    reconciled = mapped.analysis_reconcile("patterns", [request["request_id"]])
    assert reconciled["screened_pairs"] == 399
    assert reconciled["selected_pairs"] == reconciled["detailed_pairs"] == 0
    detail = mapped.analysis_request("patterns", ["app.py"], pattern_ids=[])
    assert detail["packet"]["catalogue"] == []
    mapped.submit_analysis(detail["request_id"], {"producer": "synthetic", "evaluations": []})


def test_explicit_test_and_inert_modules_can_still_get_exhaustive_reviews(mapped):
    scope = ["tests/test_app.py", "marker.py"]
    request = mapped.analysis_request("patterns", scope)
    assert len(request["packet"]["catalogue"]) == 133
    mapped.submit_analysis(request["request_id"], pattern_result(request))
    result = mapped.analysis_reconcile("patterns", [request["request_id"]], scope)
    assert result["mode"] == "exhaustive" and result["detailed_pairs"] == 266
    assert result["excluded_modules"] == []


def test_empty_production_scope_is_reported_without_a_fake_screen(tmp_path):
    (tmp_path / "test_only.py").write_text("def test_result():\n    assert True\n")
    application = Application(tmp_path)
    application.scan()
    assert plan_scopes(application._current(), "pattern-screen") == []
    result = application.analysis_reconcile("patterns", [])
    assert result["mode"] == "empty" and result["scope_status"] == "no_eligible_modules"
    assert result["screened_pairs"] == result["detailed_pairs"] == 0
    assert len(result["excluded_modules"]) == 1


def test_screen_model_meaning_excludes_volatile_metadata_and_unrelated_snapshot_changes(mapped):
    graph = mapped._current()
    request = build_request(graph, "pattern-screen", ["app.py"])
    nodes = copy.deepcopy(graph.nodes)
    for node in nodes:
        if node.kind == "module":
            node.data["dependency_implementation_changed"] = True
            node.data["annotation"].update(
                source_snapshot="volatile", request_id="volatile", execution={"noise": True}
            )
    altered = replace(
        graph,
        nodes=nodes,
        snapshot=replace(graph.snapshot, id="other", root="/another/checkout", revision="next"),
    )
    again = build_request(altered, "pattern-screen", ["app.py"])
    assert request["request_id"] != again["request_id"]
    assert input_identity(request) == input_identity(again)
    changed = replace(
        altered,
        snapshot=replace(altered.snapshot, files=altered.snapshot.files | {"app.py": "changed"}),
    )
    assert input_identity(request) != input_identity(
        build_request(changed, "pattern-screen", ["app.py"])
    )


def test_screen_reuses_after_unrelated_edits_but_changes_for_dependency_public_interface(mapped):
    request = mapped.analysis_request("pattern-screen", ["app.py"])
    mapped.submit_analysis(request["request_id"], selection_result(request))
    (mapped.root / "unrelated.py").write_text("value = 3\n")
    mapped.scan()
    again = mapped.analysis_request("pattern-screen", ["app.py"])
    assert input_identity(request) == input_identity(again)
    assert mapped.analysis_saved(again["request_id"])
    (mapped.root / "worker.py").write_text("def compute():\n    return 2\n")
    mapped.scan()
    body_edit = mapped.analysis_request("pattern-screen", ["app.py"])
    assert input_identity(again) == input_identity(body_edit)
    (mapped.root / "worker.py").write_text("def compute(value=0):\n    return value\n")
    mapped.scan()
    annotate(mapped, ["app.py"])
    signature_edit = mapped.analysis_request("pattern-screen", ["app.py"])
    assert input_identity(body_edit) != input_identity(signature_edit)


def test_default_reviews_surface_current_actions_without_detailed_essays(mapped):
    request = mapped.analysis_request("patterns", ["app.py"], pattern_ids=["adapter"])
    result = pattern_result(request)
    result["evaluations"][0].update(
        opportunity=3,
        current_fit="absent",
        proposed_change="Keep external behavior behind the existing caller interface.",
        evidence=[{"path": "app.py", "line": 1}],
        cons=["DETAIL_ONLY_COST_SENTINEL"],
    )
    mapped.submit_analysis(request["request_id"], result)
    summary = mapped.reviews("patterns")
    assert summary["opportunities"][0]["pattern_id"] == "adapter"
    assert summary["opportunities"][0]["evidence"] == [{"path": "app.py", "line": 1}]
    assert "DETAIL_ONLY_COST_SENTINEL" not in json.dumps(summary)
    assert (
        mapped.inspect("review:" + request["request_id"])["entity"]["data"]["result"]["result"]
        == result
    )


def test_selected_details_reuse_without_portable_export_after_unrelated_and_neighbor_body_edits(
    mapped,
):
    scope = ["app.py"]
    request = mapped.analysis_request("patterns", scope, pattern_ids=["adapter", "singleton"])
    assert "data" not in request["packet"]["modules"][0]
    assert len(request["packet"]["charter"]) == 10
    exhaustive = mapped.analysis_request("patterns", scope)
    assert "data" in exhaustive["packet"]["modules"][0]
    assert "charter" not in exhaustive["packet"]
    mapped.submit_analysis(request["request_id"], pattern_result(request))
    assert not (mapped.root / "anaxigraph/record.json").exists()
    (mapped.root / "unrelated.py").write_text("value = 3\n")
    mapped.scan()
    unrelated = mapped.analysis_request("patterns", scope, pattern_ids=["adapter", "singleton"])
    assert input_identity(request) == input_identity(unrelated)
    assert mapped.analysis_saved(unrelated["request_id"])
    (mapped.root / "worker.py").write_text("def compute():\n    return 2\n")
    mapped.scan()
    neighbor = mapped.analysis_request("patterns", scope, pattern_ids=["adapter", "singleton"])
    assert input_identity(unrelated) == input_identity(neighbor)
    assert mapped.analysis_saved(neighbor["request_id"])


def test_screen_rejects_a_stale_saved_selection(mapped):
    request = mapped.analysis_request("pattern-screen", ["app.py"])
    mapped.submit_analysis(request["request_id"], selection_result(request))
    (mapped.root / "app.py").write_text("value = 4\n")
    with pytest.raises(ValueError, match="changed after mapping"):
        mapped.analysis_selection(request["request_id"])
