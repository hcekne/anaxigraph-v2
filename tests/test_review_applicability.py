"""Saved advice follows matching inputs, not unrelated checkout edits or transient packets."""

import subprocess

import pytest
from test_architecture_scouts import answer as scout_answer
from test_pattern_decisions import make_application, review
from test_workflows import answer

from anaxigraph.analysis.charter import CHARTER
from anaxigraph.analysis.portable import input_identity
from anaxigraph.application import Application


def saved_review(app, kind):
    if kind == "patterns":
        return review(app)[0]
    request = app.analysis_request(kind, ["delivery.py"])
    result = scout_answer(request) if kind == "architecture" else answer(request)
    if kind == "architecture":
        result["findings"][0]["evidence"] = [{"path": "delivery.py", "line": 1}]
    app.submit_analysis(request["request_id"], result)
    return request


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize("kind", ["patterns", "architecture", "fresh-eyes"])
@pytest.mark.parametrize("edit", ["other-source", "documentation", "commit"])
def test_compatible_advice_survives_unrelated_edits_without_rewriting_its_capture(
    tmp_path, kind, edit
):
    app = make_application(tmp_path)
    if edit == "commit":
        git(tmp_path, "init", "-q")
        git(tmp_path, "config", "user.name", "Fixture")
        git(tmp_path, "config", "user.email", "fixture@example.test")
        git(tmp_path, "add", "delivery.py", "unrelated.py")
        git(tmp_path, "commit", "-qm", "Initial source")
        app.scan()
    request = saved_review(app, kind)
    before = app.prepare_change("Review architecture of delivery")
    [proposal] = before["implementation_advice"]["review_proposals"]
    captured = app.store.get_review(str(tmp_path), request["request_id"], kind)
    token = app.store.review_token(str(tmp_path))
    if edit == "other-source":
        (tmp_path / "unrelated.py").write_text("def lookup(value):\n    return str(value)\n")
    elif edit == "documentation":
        (tmp_path / "README.md").write_text("Unrelated development notes.\n")
    else:
        git(tmp_path, "commit", "--allow-empty", "-qm", "Record project work")
    after = app.prepare_change("Review architecture of delivery", continue_task=True)
    assert before["snapshot_id"] != after["snapshot_id"]
    rebuilt = app._rebuild_request(app._current(), kind, captured["result"])
    assert input_identity(rebuilt) == captured["result"]["input_identity"]
    assert after["implementation_advice"]["review_proposals"] == [proposal]
    [row] = app.reviews(kind)["items"]
    assert row["status"] == "current"
    assert row["snapshot_id"] == captured["snapshot_id"]
    assert row["checked_snapshot_id"] == after["snapshot_id"]
    assert app.store.review_token(str(tmp_path)) == token
    assert (
        app.store.get_review(str(tmp_path), request["request_id"], kind)["result"]
        == captured["result"]
    )


@pytest.mark.parametrize("kind", ["patterns", "architecture", "fresh-eyes"])
@pytest.mark.parametrize("edit", ["source", "resource", "declared-document"])
def test_changed_review_inputs_stay_stale(tmp_path, kind, edit):
    app = make_application(tmp_path)
    if edit == "declared-document":
        (tmp_path / "POLICY.md").write_text("Preserve accepted input guarantees.\n")
        app.scan()
        mapping = app.analysis_request("annotate", ["delivery.py"])
        annotation = answer(mapping)
        annotation["annotations"][0]["contracts"] = [
            {
                "statement": "Preserve accepted input guarantees.",
                "basis": "declared",
                "evidence": [{"path": "POLICY.md", "line": 1}],
            }
        ]
        app.submit_analysis(mapping["request_id"], annotation)
    request = saved_review(app, kind)
    if edit == "source":
        (tmp_path / "delivery.py").write_text("def deliver(items):\n    return sorted(items)\n")
    elif edit == "resource":
        (tmp_path / "settings.json").write_text('{"require_validation":true}\n')
    else:
        (tmp_path / "POLICY.md").write_text("New caller-visible obligations.\n")
    brief = app.prepare_change("Review architecture of delivery")
    assert brief["implementation_advice"]["review_proposals"] == []
    assert app.reviews(kind)["items"][0]["status"] == "stale"
    assert app.store.get_review(str(tmp_path), request["request_id"], kind) is not None


@pytest.mark.parametrize("kind", ["patterns", "architecture", "fresh-eyes"])
def test_current_capture_still_checks_changed_policy_and_result_validity(
    tmp_path, monkeypatch, kind
):
    app = make_application(tmp_path)
    request = saved_review(app, kind)
    row = app.store.get_review(str(tmp_path), request["request_id"], kind)
    snapshot = app._current().snapshot.id
    with monkeypatch.context() as changed:
        changed.setitem(
            CHARTER[0], "guidance", CHARTER[0]["guidance"] + " Reassess declared obligations."
        )
        assert (
            app.prepare_change("Review architecture of delivery")["implementation_advice"][
                "review_proposals"
            ]
            == []
        )
        assert app._current().snapshot.id == snapshot
    row["result"]["result"]["producer"] = ""
    app.store.save_review(str(tmp_path), snapshot, request["request_id"], kind, row["result"])
    assert (
        app.prepare_change("Review architecture of delivery")["implementation_advice"][
            "review_proposals"
        ]
        == []
    )
    assert app.reviews(kind)["items"][0]["status"] == "stale"


@pytest.mark.parametrize("kind", ["patterns", "architecture"])
def test_old_request_cannot_promote_a_changed_source_result_as_available(tmp_path, kind):
    app = make_application(tmp_path)
    request = saved_review(app, kind)
    assert app.analysis_saved(request["request_id"])
    (tmp_path / "delivery.py").write_text("def deliver(items):\n    return sorted(items)\n")
    app.scan()
    assert app.analysis_saved(request["request_id"]) is False


@pytest.mark.parametrize("kind", ["patterns", "architecture"])
def test_reusing_compatible_analysis_keeps_its_existing_decision(tmp_path, kind):
    app = make_application(tmp_path)
    request = saved_review(app, kind)
    initial = app.assess_plan("Review delivery", ["delivery.py"])
    [required] = initial["architecture"]["requirements"]
    decision = {
        "proposal_id": required["proposal_id"],
        "decision": "defer",
        "reason": "Keep this option for the next delivery milestone.",
    }
    app.assess_plan("Review delivery", ["delivery.py"], [decision])
    (tmp_path / "unrelated.py").write_text("def lookup(value):\n    return str(value)\n")
    app.prepare_change("Review delivery", continue_task=True)
    options = (
        {
            "pattern_ids": [
                item["pattern_id"]
                for item in app.store.get_review(str(tmp_path), request["request_id"], kind)[
                    "result"
                ]["result"]["evaluations"]
            ]
        }
        if kind == "patterns"
        else {}
    )
    current = app.analysis_request(kind, ["delivery.py"], **options)
    assert app.analysis_saved(current["request_id"])
    resumed = Application(tmp_path).assess_plan("Review delivery", ["delivery.py"])
    assert resumed["architecture"]["total"] == resumed["architecture"]["resolved"] == 1
    assert resumed["architecture"]["decisions"] == [decision]
    assert resumed["architecture"]["pending_ids"] == []


def test_blind_alternatives_do_not_disappear_behind_two_material_proposals(tmp_path):
    app = make_application(tmp_path)
    for kind in ("patterns", "architecture", "fresh-eyes"):
        saved_review(app, kind)
    proposals = app.prepare_change("Review architecture of delivery")["implementation_advice"][
        "review_proposals"
    ]
    assert {item["kind"] for item in proposals} == {"pattern", "architecture", "fresh-eyes"}
    [blind] = [item for item in proposals if item["kind"] == "fresh-eyes"]
    assert len(blind["alternatives"]) == 3
    ordinary = app.prepare_change("Improve delivery")["implementation_advice"]["review_proposals"]
    assert {item["kind"] for item in ordinary} == {"pattern", "architecture"}


def test_product_review_survives_transient_request_retention_but_not_revised_child_results(
    tmp_path,
):
    app = make_application(tmp_path)
    child = saved_review(app, "fresh-eyes")
    product = app.analysis_product_request([child["request_id"]])
    app.submit_analysis(product["request_id"], answer(product))
    with app.store._connection(write=True) as db:
        db.execute(
            "DELETE FROM reviews WHERE root=? AND id=? AND kind='request'",
            (str(tmp_path), child["request_id"]),
        )
    result = app.inspect("review:" + product["request_id"])["entity"]["data"]
    assert result["status"] == "current"
    current_child = app.analysis_request("fresh-eyes", ["delivery.py"])
    revised = answer(current_child)
    revised["alternatives"][0]["name"] = "A revised independent option"
    app.submit_analysis(current_child["request_id"], revised)
    result = app.inspect("review:" + product["request_id"])["entity"]["data"]
    assert result["status"] == "stale"
