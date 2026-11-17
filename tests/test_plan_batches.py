"""Large task reviews resume without losing decisions or trusting changed recommendations."""

from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_architecture_scouts import answer as scout_answer
from test_pattern_decisions import make_application, review
from test_workflows import answer

from anaxigraph.analysis.packets import catalogue
from anaxigraph.application import Application
from anaxigraph.interfaces.http import create_app
from anaxigraph.interfaces.mcp import create_mcp
from anaxigraph.storage.schema import SCHEMA_VERSION


@pytest.fixture
def application(tmp_path):
    return make_application(tmp_path)


def dispositions(plan):
    return [
        {
            "proposal_id": item["proposal_id"],
            "decision": "defer",
            "reason": "Review in a later task.",
        }
        for item in plan["architecture"]["requirements"]
    ]


@pytest.mark.parametrize("logging", [True, False])
def test_large_review_completes_across_processes_without_trial_logging(
    application, monkeypatch, logging
):
    if not logging:
        monkeypatch.setenv("ANAXIGRAPH_EVIDENCE", "off")
        application.evidence.enabled = False
    review(application, (2,) * 13)
    task, files = "review delivery", ["delivery.py"]
    initial = application.assess_plan(task, files)
    assert initial["status"] == "decision_required"
    assert initial["architecture"]["remaining"] == 13
    first_ids = {item["proposal_id"] for item in initial["architecture"]["requirements"]}
    assert len(first_ids) == 12
    resumed = Application(application.root).assess_plan(task, files, dispositions(initial))
    assert resumed["architecture"]["remaining"] == 1
    assert resumed["architecture"]["resolved"] == 12
    assert not first_ids.intersection(
        p["proposal_id"] for p in resumed["architecture"]["requirements"]
    )
    if logging:
        application.evidence.max_events = 1
        assert application.evidence.latest("architecture_decisions") is not None
        application.evidence.record("noise", {"status": "completed"})
        assert application.evidence.latest("architecture_decisions") is None
    else:
        assert not application.evidence.path.exists()
    repeated = Application(application.root).assess_plan(task, files)
    assert repeated["architecture"]["remaining"] == 1
    finished = Application(application.root).assess_plan(task, files, dispositions(repeated))
    assert finished["architecture"]["status"] == "decisions_recorded"
    assert finished["architecture"]["resolved"] == 13
    assert finished["architecture"]["remaining"] == 0
    validated = Application(application.root).validate_change()["architecture"]
    assert validated["resolved"] == 13
    assert validated["implementation_status"] == "unverified"
    assert len(validated["obligations"]) <= 12


def test_all_catalogue_pairs_can_be_accounted_for_in_bounded_batches(application):
    review(application, (2,) * 133)
    task, files = "review delivery", ["delivery.py"]
    plan = application.assess_plan(task, files)
    seen = set()
    for _ in range(12):
        if plan["architecture"]["remaining"] == 0:
            break
        choices = dispositions(plan)
        assert not seen.intersection(d["proposal_id"] for d in choices)
        seen.update(d["proposal_id"] for d in choices)
        plan = Application(application.root).assess_plan(task, files, choices)
        assert len(plan["architecture"]["requirements"]) <= 12
        assert len(plan["architecture"]["decisions"]) <= 12
    assert plan["architecture"]["status"] == "decisions_recorded"
    assert plan["architecture"]["resolved"] == len(seen) == 133
    complete = Application(application.root).inspect("plan", full=True)["entity"]["data"]
    assert len(complete["choices"]) == 133
    assert len(application.store.reviews(str(application.root), "plan")) == 1
    token = application.store.review_token(str(application.root))
    application.assess_plan(task, files)
    assert application.store.review_token(str(application.root)) == token
    assert not any(row["kind"] == "plan" for row in application.reviews()["items"])
    exported = application.export_record()
    assert exported["record_exported"]
    record = json.loads((application.root / "anaxigraph/record.json").read_text())
    assert all(item["kind"] != "plan" for item in record["analyses"])


def test_older_material_review_is_not_hidden_by_newer_optional_reviews(application):
    for index in range(25):
        request = application.analysis_request(
            "patterns", ["delivery.py"], pattern_ids=[catalogue()[index]["id"]]
        )
        result = answer(request)
        result["evaluations"][0].update(
            opportunity=3 if index == 0 else 1,
            proposed_change="Inspect capacity before accepting new work.",
            evidence=[{"path": "delivery.py", "line": 1}],
        )
        application.submit_analysis(request["request_id"], result)
    brief = application.prepare_change("change delivery")
    assert brief["implementation_advice"]["review_proposals"][0]["opportunity"] == 3
    plan = application.assess_plan("change delivery", ["delivery.py"])
    assert plan["status"] == "decision_required"
    assert plan["architecture"]["total"] == 1


def test_changed_meaning_requires_a_new_decision_even_beyond_the_display_excerpt(application):
    request, result = review(application)
    prefix = "Preserve the existing behavior while changing admission carefully. " * 8
    result["evaluations"][0]["proposed_change"] = prefix + "Use small local batches."
    application.submit_analysis(request["request_id"], result)
    initial = application.assess_plan("review delivery", ["delivery.py"])
    [original] = initial["architecture"]["requirements"]
    application.assess_plan("review delivery", ["delivery.py"], dispositions(initial))
    result["evaluations"][0]["proposed_change"] = prefix + "Introduce a remote queue and workers."
    application.submit_analysis(request["request_id"], result)
    updated = Application(application.root).assess_plan("review delivery", ["delivery.py"])
    assert updated["status"] == "decision_required"
    [current] = updated["architecture"]["requirements"]
    assert current["suggestion"] == original["suggestion"]
    assert current["proposal_id"] != original["proposal_id"]
    assert updated["architecture"]["remaining"] == 1


def test_reordered_architecture_findings_keep_identity_but_changed_contract_does_not(application):
    request = application.analysis_request("architecture", ["delivery.py"])
    result = scout_answer(request)
    result["findings"][0]["evidence"] = [{"path": "delivery.py", "line": 1}]
    second = copy.deepcopy(result["findings"][0])
    second.update(title="Separate admission from execution", next_step="Inspect admission callers.")
    result["findings"].append(second)
    application.submit_analysis(request["request_id"], result)
    initial = application.prepare_change("change delivery")["implementation_advice"][
        "review_proposals"
    ]
    result["findings"].reverse()
    application.submit_analysis(request["request_id"], result)
    reordered = application.prepare_change("change delivery")["implementation_advice"][
        "review_proposals"
    ]
    assert {p["title"]: p["proposal_id"] for p in initial} == {
        p["title"]: p["proposal_id"] for p in reordered
    }
    result["findings"][0]["public_contract"] = (
        "Preserve ordered delivery and cancellation failures."
    )
    application.submit_analysis(request["request_id"], result)
    changed = application.prepare_change("change delivery")["implementation_advice"][
        "review_proposals"
    ]
    assert {p["proposal_id"] for p in reordered} != {p["proposal_id"] for p in changed}


def test_an_edit_does_not_erase_previously_assessed_obligations(application):
    review(application)
    initial = application.assess_plan("review delivery", ["delivery.py"])
    application.assess_plan("review delivery", ["delivery.py"], dispositions(initial))
    (application.root / "delivery.py").write_text("def deliver(items):\n    return sorted(items)\n")
    resumed = Application(application.root).assess_plan("review delivery", ["delivery.py"])
    assert resumed["architecture"]["resolved"] == 1
    checked = Application(application.root).validate_change()["architecture"]
    assert checked["obligations"] and checked["implementation_status"] == "unverified"
    application.prepare_change("start a genuinely new task")
    assert application.validate_change()["architecture"]["status"] == "not_assessed"


def test_late_plan_write_cannot_overwrite_a_newer_disposition(application, monkeypatch):
    review(application)
    initial = application.assess_plan("review delivery", ["delivery.py"])
    other = Application(application.root)
    original = application.store.save_review

    def interleave(*args, **kwargs):
        if args[3] == "plan":
            other.assess_plan("review delivery", ["delivery.py"], dispositions(initial))
        return original(*args, **kwargs)

    monkeypatch.setattr(application.store, "save_review", interleave)
    mine = dispositions(initial)
    mine[0]["decision"] = "adopt"
    with pytest.raises(ValueError, match="checkpoint changed"):
        application.assess_plan("review delivery", ["delivery.py"], mine)
    state = other.inspect("plan", full=True)["entity"]["data"]
    assert state["choices"][0]["decision"] == "defer"


def test_new_task_prevents_an_older_first_assessment_from_being_published(application, monkeypatch):
    review(application)
    other = Application(application.root)
    original = application.store.save_review

    def interleave(*args, **kwargs):
        if args[3] == "plan":
            other.prepare_change("a different task")
        return original(*args, **kwargs)

    monkeypatch.setattr(application.store, "save_review", interleave)
    with pytest.raises(ValueError, match="Task baseline changed"):
        application.assess_plan("review delivery", ["delivery.py"])
    assert other.validate_change()["architecture"]["status"] == "not_assessed"


def test_version_eight_upgrade_backs_up_and_preserves_saved_analyses(application):
    request, _ = review(application)
    before = application.store.get_review(str(application.root), request["request_id"], "patterns")
    application.prepare_change("review delivery")
    with sqlite3.connect(application.store.path) as db:
        db.execute("PRAGMA user_version=8")
    upgraded = Application(application.root)
    backup = application.store.path.with_name(
        application.store.path.name + f".pre-v{SCHEMA_VERSION}.sqlite"
    )
    assert backup.exists()
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 8
    assert (
        upgraded.store.get_review(str(application.root), request["request_id"], "patterns")
        == before
    )
    assert upgraded.status()["annotations"]["current"] == 2
    assert upgraded.assess_plan("review delivery", ["delivery.py"])["status"] == "decision_required"


def test_legacy_ledger_plan_remains_visible_without_treating_a_static_predicate_as_implementation(
    application,
):
    prepared = application.prepare_change("review delivery")
    application.evidence.record(
        "architecture_plan",
        {
            "baseline_id": prepared["baseline_id"],
            "snapshot_id": prepared["snapshot_id"],
            "status": "decisions_recorded",
            "metrics": {
                "decisions": [],
                "pending_ids": [],
                "truncated": False,
                "obligations": [
                    {"proposal_id": "legacy:0", "predicate": "no-static-registrar-enumeration"}
                ],
            },
        },
    )
    checked = Application(application.root).validate_change()["architecture"]
    assert checked["status"] == "decisions_recorded"
    assert checked["obligations"][0]["status"] == "unresolved"


def test_http_and_dashboard_walk_both_batches_and_inspect_the_complete_plan(application, tmp_path):
    review(application, (2,) * 13)
    client = TestClient(
        create_app(application), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    )
    request = {"task": "review delivery", "files": ["delivery.py"]}
    initial = client.post("/api/assess", json=request).json()
    batch = dispositions(initial)
    second = client.post("/api/assess", json=request | {"decisions": batch}).json()
    last = dispositions(second)
    final = client.post("/api/assess", json=request | {"decisions": last}).json()
    assert final["architecture"]["resolved"] == 13
    details = client.get("/api/entity", params={"entity_id": "plan", "full": True}).json()
    assert len(details["entity"]["data"]["choices"]) == 13
    packets = tmp_path / "dashboard-plan-packets.json"
    packets.write_text(json.dumps({"initial": initial, "second": second, "final": final}))
    script = Path(__file__).with_name("plan_dashboard.cjs")
    result = subprocess.run(
        ["node", str(script), str(packets)], check=True, capture_output=True, text=True
    )
    submitted = json.loads(result.stdout)
    assert {d["proposal_id"] for d in submitted["decisions"]} == {
        d["proposal_id"] for d in (*batch, *last)
    }
    assert submitted["inspected"] == ["plan"]


def test_mcp_uses_the_same_resumable_plan_and_inspection_contract(application):
    review(application, (2,) * 13)

    async def run():
        server = create_mcp(application)

        async def call(name, arguments):
            result = await server.call_tool(name, arguments)
            return json.loads(result[0].text)

        request = {"task": "review delivery", "files": ["delivery.py"]}
        initial = await call("assess_plan", request)
        second = await call("assess_plan", request | {"decisions": dispositions(initial)})
        assert second["architecture"]["remaining"] == 1
        final = await call("assess_plan", request | {"decisions": dispositions(second)})
        assert final["architecture"]["resolved"] == 13
        expanded = await call("inspect", {"entity_id": "plan", "full": True})
        assert len(expanded["entity"]["data"]["choices"]) == 13

    asyncio.run(run())
