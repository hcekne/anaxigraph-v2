"""Material scout findings require dispositions without inventing completed changes."""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_architecture_scouts import answer as scout_answer
from test_workflows import answer

from anaxigraph.analysis.architecture_guidance import decide
from anaxigraph.analysis.guidance import prepare
from anaxigraph.application import Application
from anaxigraph.workflows.engine import WorkflowRunner


@pytest.fixture
def application(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/a.py").write_text("def produce(value):\n    return value\n")
    (tmp_path / "pkg/b.py").write_text(
        "from .a import produce\ndef deliver(value):\n    return produce(value)\n"
    )
    app = Application(tmp_path)
    mapped = WorkflowRunner(app, lambda request, *_args, **_kwargs: answer(request)).start(
        "mapping", background=False
    )
    assert mapped["status"] == "completed"
    request = app.analysis_request("architecture", ["pkg/a.py", "pkg/b.py"], lens="packages")
    app.submit_analysis(request["request_id"], scout_answer(request))
    return app


def test_ordinary_change_gets_current_scout_advice_without_architecture_keyword(application):
    brief = application.prepare_change("change produce")
    proposals = brief["implementation_advice"]["review_proposals"]
    assert proposals and proposals[0]["kind"] == "architecture"
    assert proposals[0]["public_contract"]
    assert proposals[0]["options"][0]["placement"] == proposals[0]["placement"]
    requirements = application.assess_plan("change produce", ["pkg/a.py"])["architecture"][
        "requirements"
    ]
    assert len(requirements[0]["options"]) == 4
    assert brief["implementation_advice"]["architecture_review"]["status"] == "partial"
    assert "two reference levels" in " ".join(brief["uncertainty"])


def test_large_context_keeps_a_scout_action_in_the_default_brief(application):
    graph = application._current()
    nodes = []
    for node in graph.nodes:
        if node.kind == "module":
            annotation = node.data["annotation"] | {
                "contracts": [
                    {
                        "statement": "Preserve the observable result and caller behavior. " * 20,
                        "basis": "inferred",
                        "evidence": [{"path": node.path, "line": 1}],
                    }
                ]
                * 30
            }
            node = replace(node, data=node.data | {"annotation": annotation})
        nodes.append(node)
    graph = replace(graph, nodes=tuple(nodes))
    rows = application.store.reviews(str(application.root), "architecture")
    brief = prepare(
        graph,
        application.store.search(str(application.root), "produce", 3),
        "change produce " * 200,
        rows,
    )
    assert brief["context_truncated"]
    assert brief["implementation_advice"]["review_proposals"][0]["kind"] == "architecture"
    assert brief["likely_owners"][0]["path"] in {"pkg/a.py", "pkg/b.py"}


@pytest.mark.parametrize("decision", ["adopt", "defer", "reject"])
def test_plan_cannot_record_material_findings_as_addressed_without_a_reason(application, decision):
    initial = application.assess_plan("change produce", ["pkg/a.py"])
    assert initial["status"] == initial["architecture"]["status"] == "decision_required"
    proposal = initial["architecture"]["requirements"][0]
    supplied = [
        {
            "proposal_id": proposal["proposal_id"],
            "decision": decision,
            "reason": "Evaluate this seam against the existing consumer before changing it.",
        }
    ]
    result = application.assess_plan("change produce", ["pkg/a.py"], supplied)
    assert result["status"] == "advisory"
    assert result["architecture"]["status"] == "decisions_recorded"
    assert result["architecture"]["recorded"]
    assert result["architecture"]["pending_ids"] == []
    resumed = Application(application.root)
    validated = resumed.validate_change()
    assert validated["architecture"]["decisions"] == supplied
    assert validated["architecture"]["implementation_status"] == "unverified"
    assert validated["behavioral_status"] == "unchecked"
    resumed.prepare_change("another task")
    assert resumed.validate_change()["architecture"]["status"] == "not_assessed"


def test_supplied_passing_check_does_not_attest_that_the_architecture_was_implemented(application):
    result = application.assess_plan("change produce", ["pkg/a.py"])
    proposal_id = result["architecture"]["requirements"][0]["proposal_id"]
    application.assess_plan(
        "change produce",
        ["pkg/a.py"],
        [
            {
                "proposal_id": proposal_id,
                "decision": "adopt",
                "reason": "Implement a local package boundary.",
            }
        ],
    )
    snapshot = application.status()["snapshot"]["id"]
    checked = application.validate_change(
        [{"command": "pytest", "snapshot_id": snapshot, "exit_code": 0}]
    )
    assert checked["behavioral_status"] == "reported-passed"
    assert checked["architecture"]["implementation_status"] == "unverified"
    (application.root / "pkg/a.py").write_text("def produce(value):\n    return value + 1\n")
    current = application.scan()["snapshot"]["id"]
    assert (
        application.validate_change(baseline_id=current)["architecture"]["status"]
        == "baseline_changed"
    )


@pytest.mark.parametrize("change", ["reason", "duplicate", "foreign", "decision"])
def test_invalid_dispositions_are_rejected_not_silently_ignored(application, change):
    result = application.assess_plan("change produce", ["pkg/a.py"])
    identity = result["architecture"]["requirements"][0]["proposal_id"]
    item = {"proposal_id": identity, "decision": "defer", "reason": "Outside this change's scope."}
    items = [item]
    if change == "duplicate":
        items.append(item.copy())
    else:
        item[{"reason": "reason", "foreign": "proposal_id", "decision": "decision"}[change]] = {
            "reason": " ",
            "foreign": "unseen:0",
            "decision": "ignore",
        }[change]
    with pytest.raises(ValueError, match="Architecture"):
        application.assess_plan("change produce", ["pkg/a.py"], items)


def test_truncated_material_scope_is_not_reported_as_fully_decided():
    result = decide([], [], truncated=True)
    assert result["status"] == "scope_incomplete"
    assert result["implementation_status"] == "unverified"


def test_refined_task_explicitly_continued_keeps_plan_followthrough(application):
    result = application.assess_plan("change produce", ["pkg/a.py"])
    identity = result["architecture"]["requirements"][0]["proposal_id"]
    decided = application.assess_plan(
        "change produce",
        ["pkg/a.py"],
        [
            {
                "proposal_id": identity,
                "decision": "defer",
                "reason": "Preserve the current boundary for this task.",
            }
        ],
    )
    initial = application.trial_evidence()["summary"]["active_task_id"]
    continued = application.prepare_change("refined produce task", continue_task=True)
    repeated = application.prepare_change("refined produce task")
    assert continued["baseline_id"] == repeated["baseline_id"] == decided["brief"]["baseline_id"]
    assert continued["task_id"] == repeated["task_id"] == initial
    assert application.validate_change()["architecture"]["status"] == "decisions_recorded"
