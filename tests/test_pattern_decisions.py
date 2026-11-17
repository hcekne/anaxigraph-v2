"""Detailed Pattern advice reaches task decisions and survives through validation."""

from __future__ import annotations

import shutil

import pytest
from test_architecture_scouts import answer as scout_answer
from test_pattern_screening import annotate
from test_workflows import answer

from anaxigraph.analysis.packets import catalogue
from anaxigraph.application import Application


def make_application(tmp_path):
    (tmp_path / "delivery.py").write_text("def deliver(items):\n    return list(items)\n")
    (tmp_path / "unrelated.py").write_text("def lookup(value):\n    return value\n")
    app = Application(tmp_path)
    app.scan()
    annotate(app)
    return app


@pytest.fixture
def application(tmp_path):
    return make_application(tmp_path)


def review(app, scores=(3,), path="delivery.py"):
    request = app.analysis_request(
        "patterns", [path], pattern_ids=[card["id"] for card in catalogue()[: len(scores)]]
    )
    result = answer(request)
    for item, score in zip(result["evaluations"], scores, strict=True):
        item.update(
            opportunity=score,
            proposed_change="Keep work creation within the caller's configured capacity.",
            pros=["Contains resource pressure."],
            cons=["Adds scheduling coordination."],
            alternative="Process a small fixed batch before accepting another.",
            evidence=[{"path": path, "line": 1}],
        )
    app.submit_analysis(request["request_id"], result)
    return request, result


@pytest.mark.parametrize("decision", ["adopt", "defer", "reject"])
def test_pattern_is_the_same_action_in_preparation_assessment_and_followthrough(
    application, decision
):
    request, _ = review(application)
    brief = application.prepare_change("change delivery")
    [shown] = brief["implementation_advice"]["review_proposals"]
    plan = application.assess_plan("change delivery", ["delivery.py"])
    assert plan["status"] == "decision_required"
    [required] = plan["architecture"]["requirements"]
    assert required["proposal_id"] == shown["proposal_id"]
    assert required["kind"] == "pattern" and required["review_id"] == request["request_id"]
    assert required["module_ids"] == ["module:delivery.py"]
    assert required["evidence"] and required["pros"] and required["cons"]
    disposition = {
        "proposal_id": required["proposal_id"],
        "decision": decision,
        "reason": "Assess this opportunity against the actual delivery contract.",
    }
    decided = application.assess_plan("change delivery", ["delivery.py"], [disposition])
    assert decided["architecture"]["status"] == "decisions_recorded"
    checked = Application(application.root).validate_change()["architecture"]
    assert checked["decisions"] == [disposition]
    [obligation] = checked["obligations"]
    assert obligation["proposal_id"] == shown["proposal_id"]
    assert obligation["status"] == obligation["proposal_status"] == "unresolved"
    assert checked["implementation_status"] == "unverified"


@pytest.mark.parametrize("score", [None, 0, 1])
def test_unknown_zero_and_optional_opportunities_do_not_require_decisions(application, score):
    review(application, (score,))
    plan = application.assess_plan("change delivery", ["delivery.py"])
    assert not plan["architecture"]["requirements"]
    assert plan["status"] == "advisory"


def test_stale_and_unrelated_pattern_findings_cannot_block_the_plan(application):
    review(application)
    review(application, path="unrelated.py")
    (application.root / "delivery.py").write_text("def deliver(items):\n    return sorted(items)\n")
    plan = application.assess_plan("change delivery", ["delivery.py"])
    assert not plan["architecture"]["requirements"]
    assert plan["status"] == "advisory"


def test_pair_identity_does_not_depend_on_model_result_order(application):
    request, result = review(application, (2, 3, 1))
    before = application.assess_plan("change delivery", ["delivery.py"])["architecture"]
    assert len(before["requirements"]) == 2
    assert before["requirements"][0]["opportunity"] == 3
    result["evaluations"].reverse()
    application.submit_analysis(request["request_id"], result)
    after = application.assess_plan("change delivery", ["delivery.py"])["architecture"]
    assert {p["proposal_id"] for p in before["requirements"]} == {
        p["proposal_id"] for p in after["requirements"]
    }


def test_clone_reuses_existing_pattern_result_and_delivers_its_material_action(
    application, tmp_path
):
    review(application)
    application.export_record()
    clone = tmp_path / "clone"
    clone.mkdir()
    for path in ("delivery.py", "unrelated.py"):
        shutil.copy2(application.root / path, clone / path)
    shutil.copytree(application.root / "anaxigraph", clone / "anaxigraph")
    target = Application(clone)
    target.scan()
    plan = target.assess_plan("change delivery", ["delivery.py"])
    assert plan["status"] == "decision_required"
    assert len(plan["architecture"]["requirements"]) == 1


def test_pattern_cannot_be_silently_displaced_by_lower_priority_architecture_advice(application):
    review(application)
    request = application.analysis_request("architecture", ["delivery.py"])
    result = scout_answer(request)
    result["findings"][0].update(priority=2, evidence=[{"path": "delivery.py", "line": 1}])
    application.submit_analysis(request["request_id"], result)
    brief = application.prepare_change("change delivery")
    assert brief["implementation_advice"]["review_proposals"][0]["kind"] == "pattern"
    requirements = application.assess_plan("change delivery", ["delivery.py"])["architecture"][
        "requirements"
    ]
    assert [p["kind"] for p in requirements] == ["pattern", "architecture"]


def test_recent_blind_reviews_do_not_hide_a_material_pattern(application):
    review(application)
    for index in range(8):
        request = application.analysis_request(
            "fresh-eyes", ["delivery.py"], max_result_bytes=20_000 + index
        )
        application.submit_analysis(request["request_id"], answer(request))
    brief = application.prepare_change("change delivery")
    assert brief["implementation_advice"]["review_proposals"][0]["kind"] == "pattern"
    plan = application.assess_plan("change delivery", ["delivery.py"])
    assert plan["status"] == "decision_required"


def test_pattern_disposition_requires_a_reason(application):
    review(application)
    plan = application.assess_plan("change delivery", ["delivery.py"])
    [proposal] = plan["architecture"]["requirements"]
    with pytest.raises(ValueError, match="reason"):
        application.assess_plan(
            "change delivery",
            ["delivery.py"],
            [{"proposal_id": proposal["proposal_id"], "decision": "adopt", "reason": " "}],
        )
