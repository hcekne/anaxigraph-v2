"""Adversarial evaluator qualification, not live coding-model results."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
campaign, delivery, fixture, oracle = (
    importlib.import_module("experiments.drift." + name)
    for name in ("campaign", "delivery", "fixture", "oracle")
)


def seeded(tmp_path, stage=0, *, coupled=False):
    spec = fixture.specification(stage, fixture.identities(8, 8))
    root = tmp_path / "candidate"
    fixture.write(root, fixture.files(spec, coupled=coupled))
    return root, spec


def test_scope_uses_real_coupling_and_fail_full_mutants(tmp_path):
    root, spec = seeded(tmp_path, 9, coupled=True)
    provider = spec["ids"]["providers"][1]
    policy_path = root / "anaxigraph/ci-policy.json"
    policy = json.loads(policy_path.read_text())
    first = spec["ids"]["providers"][0]
    for target in policy["targets"]:
        if target["id"] == "p:" + first:
            target["inputs"].remove(f"providers/{provider}.py")
    policy_path.write_text(json.dumps(policy))
    planned = campaign.plan(root, [f"providers/{provider}.py"], {"python": "test"})
    result = oracle.evaluate(root, spec, campaign.selected(planned), planned)
    assert "p:" + first in result["missing_checks"]
    assert "missing_required_checks" in result["hard_failures"]
    assert result["synthetic_cost_model"]["efficiency_credit"] is None
    assert any(not row["passed"] for row in result["fail_full_mutant"]["checks"].values())


def test_always_all_safe_but_has_no_efficiency_credit(tmp_path):
    root, spec = seeded(tmp_path)
    planned = campaign.plan(
        root, ["providers/" + spec["ids"]["providers"][0] + ".py"], {"python": "test"}
    )
    result = oracle.evaluate(root, spec, oracle.checks(spec), planned)
    assert result["qualified"], result["hard_failures"]
    assert result["synthetic_cost_model"]["efficiency_credit"] == 0
    assert all(
        "command" in row and row["exit_code"] == 0
        for row in result["selected_execution"]["checks"].values()
    )
    assert any(not row["passed"] for row in result["selected_mutant_execution"]["checks"].values())


def test_unknown_scope_and_stale_evidence_never_earn_savings(tmp_path):
    root, spec = seeded(tmp_path)
    planned = campaign.plan(root, ["unknown/runtime.json"], {"python": "test"})
    result = oracle.evaluate(
        root,
        spec,
        oracle.checks(spec),
        planned,
        evidence={"candidate_digest": "old", "check_ids": oracle.checks(spec)},
    )
    assert {"unresolved_scope", "stale_evidence"} <= set(result["hard_failures"])
    assert result["synthetic_cost_model"]["efficiency_credit"] is None


@pytest.mark.parametrize("tamper", ["command", "adapter", "gate", "target"])
def test_check_skipping_and_noop_commands_are_rejected(tmp_path, tamper):
    root, spec = seeded(tmp_path)
    path = root / "anaxigraph/ci-policy.json"
    policy = json.loads(path.read_text())
    if tamper == "command":
        policy["targets"][1]["command"] = "true"
    elif tamper == "adapter":
        (root / "checks.py").write_text("print('all checks passed')\n")
    elif tamper == "gate":
        policy["always"] = []
    else:
        policy["targets"].pop()
    path.write_text(json.dumps(policy))
    result = oracle.run(root, spec, ["gate"])
    assert not result["checks"]["gate"]["passed"]


def test_hidden_inputs_and_symlinks_do_not_silently_reuse_identity(tmp_path):
    root, spec = seeded(tmp_path)
    fixture.write(root, {".runtime/config.json": '{"mode":1}'})
    before = fixture.digest(oracle.snapshot(root))
    (root / ".runtime/config.json").write_text('{"mode":2}')
    assert fixture.digest(oracle.snapshot(root)) != before
    (root / "alias.py").symlink_to(root / "solution.py")
    assert not oracle.run(root, spec, ["gate"])["checks"]["gate"]["passed"]


def test_new_provider_builds_do_not_rebuild_unrelated_providers(tmp_path):
    root, previous = seeded(tmp_path, 23)
    runtime = {"python": "test"}
    before = delivery.artifacts(root, previous, runtime)
    current = fixture.specification(24, previous["ids"])
    fixture.advance(root, previous, current, coupled=False)
    after = delivery.artifacts(root, current, runtime)
    changed = {key for key in after if before[key]["digest"] != after[key]["digest"]}
    assert changed == {"host", current["ids"]["providers"][-2]}
    planned = campaign.plan(root, [f"providers/{current['ids']['providers'][-2]}.py"], runtime)
    assert len(campaign.selected(planned)) < len(oracle.checks(current))


def test_failed_release_keeps_pending_changes_and_exact_rollback_checkpoint(tmp_path):
    root, prior = seeded(tmp_path, -1)
    runtime = {"python": "test"}
    checkpoint = {
        "baseline": oracle.snapshot(root),
        "retained": None,
        "artifacts": delivery.artifacts(root, prior, runtime),
        "release_stage": -1,
        "release_state": None,
    }
    tree = tmp_path / "previous"
    delivery.preserve(root, tree)
    original_tree = oracle.snapshot(tree)
    current = fixture.specification(0, prior["ids"])
    changed = fixture.advance(root, prior, current, coupled=False)
    assert changed
    assert not campaign.promote(
        checkpoint,
        root,
        current,
        {"qualified": False, "retained": {"opaque": "observed"}},
        runtime,
        tree,
    )
    assert checkpoint["release_stage"] == -1 and oracle.snapshot(tree) == original_tree
    newer = fixture.specification(1, prior["ids"])
    fixture.advance(root, current, newer, coupled=False)
    pending = campaign.coding.delta(checkpoint["baseline"], oracle.snapshot(root))["changed_paths"]
    assert set(changed) <= set(pending), "Unpublished changes vanished from next CI selection"
    assert campaign.promote(
        checkpoint,
        root,
        newer,
        {"qualified": True, "retained": {"opaque": "updated"}},
        runtime,
        tree,
    )
    assert checkpoint["release_stage"] == 1 and checkpoint["release_state"] == {"opaque": "updated"}


def test_artifact_identity_requires_exact_tested_bytes_and_rollback_digest(tmp_path):
    root, spec = seeded(tmp_path, 0)
    runtime = {"python": "test"}
    previous = delivery.artifacts(root, spec, runtime)
    proposal = {
        "selected": oracle.checks(spec),
        "deploy": [*spec["ids"]["providers"], "host"],
        "unknowns": [],
    }
    evidence = delivery.receipt(root, spec, proposal["selected"], runtime, previous)
    evidence["artifact_digests"][spec["ids"]["providers"][0]] = "mutable-tag-latest"
    result = delivery.verify(root, spec, proposal, evidence, runtime, previous)
    assert (
        not result["passed"] and "artifact_or_verification_identity_mismatch" in result["failures"]
    )


def test_relative_provider_imports_are_real_artifact_inputs(tmp_path):
    root, spec = seeded(tmp_path, 8, coupled=True)
    first, second = spec["ids"]["providers"][:2]
    path = root / f"providers/{first}.py"
    path.write_text(path.read_text().replace("from providers.", "from ."))
    before = delivery.artifacts(root, spec, {})
    neighbor = root / f"providers/{second}.py"
    neighbor.write_text(neighbor.read_text() + "\n# actual consumed source revision\n")
    after = delivery.artifacts(root, spec, {})
    assert before[first]["digest"] != after[first]["digest"]


def test_shared_helper_can_move_without_a_gold_internal_layout(tmp_path):
    root, spec = seeded(tmp_path, 8)
    old = root / "shared/codec.py"
    (root / "text_implementation.py").write_text(old.read_text())
    old.unlink()
    changed = ["shared/codec.py", "text_implementation.py"]
    for name in spec["active"]:
        path = root / f"providers/{name}.py"
        path.write_text(path.read_text().replace("from shared.codec", "from text_implementation"))
        changed.append(path.relative_to(root).as_posix())
    policy_path = root / "anaxigraph/ci-policy.json"
    policy = json.loads(policy_path.read_text())
    for target in policy["targets"]:
        if target["id"].startswith("p:"):
            target["inputs"].append("text_implementation.py")
    policy_path.write_text(json.dumps(policy))
    planned = campaign.plan(root, changed, {"python": "test"})
    result = oracle.evaluate(root, spec, campaign.selected(planned), planned)
    assert result["qualified"], result["hard_failures"]


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"selected": [], "deploy": [], "unknowns": [], "passed": True},
        {"selected": ["gate", "gate"], "deploy": [], "unknowns": []},
        {"selected": ["made-up"], "deploy": [], "unknowns": []},
    ],
)
def test_strict_submission_rejects_fabricated_or_ambiguous_evidence(tmp_path, value):
    _, spec = seeded(tmp_path)
    with pytest.raises(ValueError):
        oracle.submission(value, spec)


def test_export_hides_future_tasks_and_answers(tmp_path):
    state = tmp_path / "session"
    campaign.export(state, components=8, integrations=8)
    request = json.loads((state / "control/TASK.json").read_text())
    assert request["stage"] == 0 and request["earlier_requirements"] == []
    assert "selected" not in request and "required_checks" not in request
    assert fixture.issue(1, fixture.identities(8, 8)) not in json.dumps(request)
    assert not list((state / "control").rglob("*oracle*"))
    with pytest.raises(ValueError):
        campaign.export(state, components=8, integrations=8)


def test_external_session_rejects_planner_runtime_drift(tmp_path, monkeypatch):
    state = tmp_path / "session"
    campaign.export(state, components=8, integrations=8)
    identity = campaign.runtime_identity()
    assert len(identity["planner_source_sha256"]) == 64
    monkeypatch.setattr(
        campaign, "runtime_identity", lambda: identity | {"planner_source_sha256": "changed"}
    )
    with pytest.raises(ValueError, match="runtime drift"):
        campaign.grade(state)


def test_mocked_live_driver_executes_real_two_stage_acceptance(tmp_path, monkeypatch):
    runtime = tmp_path / "anaxigraph"
    runtime.write_text("frozen mock client runtime")
    counts = {}
    calls = []

    def preflight(config):
        assert config.live and config.coding_effort == "high"
        return {"status": "passed", "evidence_kind": "mock-transport"}

    monkeypatch.setattr(
        campaign.coding,
        "client_identity",
        lambda config: {"available": config.live, "version": "mock"},
    )
    monkeypatch.setattr(campaign.coding, "preflight", preflight)
    monkeypatch.setattr(
        campaign.coding, "freeze_advice", lambda *args: {"reviews": [], "model_receipts": []}
    )

    def agent_call(root, directory, runtime, prompt, config):
        request = json.loads(prompt)
        calls.append(request)
        stage = counts.get(str(root), 0)
        ids = fixture.identities(8, 8)
        prior, current = fixture.specification(stage - 1, ids), fixture.specification(stage, ids)
        changed = fixture.advance(root, prior, current, coupled=False, previous_coupled=stage == 0)
        plan = campaign.plan(root, changed, campaign.runtime_identity())
        (root / "submission.json").write_text(
            json.dumps(
                {
                    "selected": campaign.selected(plan),
                    "deploy": [*ids["providers"], "host"],
                    "unknowns": [],
                }
            )
        )
        counts[str(root)] = stage + 1
        return {
            "status": "completed",
            "control_isolation": {"passed": True, "violations": []},
            "tools": [],
            "tokens": None,
            "evidence_kind": "mock-transport",
        }

    monkeypatch.setattr(campaign.coding, "agent_call", agent_call)
    config = SimpleNamespace(
        state=tmp_path / "live",
        runtime=runtime,
        agent="codex",
        components=8,
        integrations=8,
        seed=7103,
        episodes=2,
        call_timeout=1,
    )
    result = campaign.live(config)
    assert result["status"] == "completed", result
    assert len(calls) == 4
    assert all(request["reasoning_effort"] == "high" for request in calls)
    assert all(
        len(rows) == 2 and all(row["outcome"]["qualified"] for row in rows)
        for rows in result["arms"].values()
    )
