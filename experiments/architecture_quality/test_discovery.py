"""Offline discovery calibration; passing a monolith is not evidence of good architecture."""

from __future__ import annotations

import json

import pytest

from . import discovery


@pytest.mark.parametrize("modular", [False, True])
def test_discovery_feature_chain_and_actual_opaque_state_survive_edits(tmp_path, modular):
    discovery.seed(tmp_path)
    previous = discovery.capture_state(tmp_path)
    assert previous["response"]["quantity"] == 1
    for stage in range(4):
        discovery.synthetic_step(tmp_path, discovery.NAME, stage, modular)
        outcome = discovery.evaluate(
            tmp_path, discovery.NAME, stage, random_seed=81, previous_state=previous
        )
        assert outcome["qualified"], outcome.get("hidden", outcome)
        assert outcome["behavior_passed"] and outcome["ci_safe"]
        assert outcome["hidden"]["cross_stage_state_compatibility"] == "checked"
        assert outcome["architecture_quality"].startswith("unscored")
        previous = outcome["hidden"]["compatibility_fixture"]


def test_no_future_tasks_or_gold_architecture_leak_into_original_seed(tmp_path):
    discovery.seed(tmp_path)
    initial = "\n".join(path.read_text() for path in tmp_path.rglob("*.py"))
    for future in ("FEATURE_STAGE", "_cancel", "refund", "authorization_id", "refunded"):
        assert future not in initial
    assert not (tmp_path / "components.json").exists()
    assert "quantity" in discovery.task(discovery.NAME, 0)
    for future in ("refund", "cancel", "125", "-5", "authorization_id"):
        assert future not in discovery.brief()
    for stage in (0, 1):
        discovery.synthetic_step(tmp_path, discovery.NAME, stage, False)
        current = (tmp_path / "solution.py").read_text()
        assert "FEATURE_STAGE" not in current
        assert "_cancel" not in current and "refund" not in current
        if stage == 0:
            assert "authorization_id" not in current


def test_monolith_and_separated_fixture_both_pass_with_different_observed_change_inputs(tmp_path):
    observations = {}
    for modular in (False, True):
        root = tmp_path / str(modular)
        discovery.seed(root)
        discovery.synthetic_step(root, discovery.NAME, 0, modular)
        outcome = discovery.evaluate(root, discovery.NAME, 0, random_seed=20)
        assert outcome["qualified"]
        observations[modular] = outcome["observational_evidence"]["executed_inputs"]
    assert (
        observations[False]["reservations"]
        == observations[False]["notifications"]
        == observations[False]["reports"]
    )
    assert observations[True]["reservations"] != observations[True]["notifications"]
    assert observations[True]["notifications"] != observations[True]["reports"]


@pytest.mark.parametrize("bug", ["atomicity", "partial-notification-progress", "duplicate-charge"])
def test_hidden_sequence_oracle_catches_bugs_not_exercised_by_candidate_examples(tmp_path, bug):
    discovery.seed(tmp_path)
    discovery.synthetic_step(tmp_path, discovery.NAME, 0, False)
    source = tmp_path / "solution.py"
    text = source.read_text()
    if bug == "atomicity":
        text = text.replace(
            "    try:\n        answer = provider('charge'",
            "    _commit(state, draft)\n    try:\n        answer = provider('charge'",
            1,
        )
    elif bug == "partial-notification-progress":
        text = text.replace("            _commit(state, copy.deepcopy(draft))", "            pass")
    else:
        text = text.replace(
            "    if identity in draft['bookings']:",
            "    if identity in draft['bookings']:\n        provider('charge', {'duplicate': identity})",
            1,
        )
    source.write_text(text)
    outcome = discovery.evaluate(tmp_path, discovery.NAME, 0, random_seed=32)
    assert all(check["passed"] for check in outcome["candidate_checks"].values())
    assert not outcome["behavior_passed"]
    assert not outcome["qualified"]


def test_shared_state_and_configuration_cannot_be_under_tested(tmp_path):
    discovery.seed(tmp_path)
    discovery.synthetic_step(tmp_path, discovery.NAME, 0, True)
    (tmp_path / "ci_select.py").write_text("import json\nprint(json.dumps(['smoke']))\n")
    outcome = discovery.evaluate(tmp_path, discovery.NAME, 0, random_seed=11)
    assert outcome["behavior_passed"]
    assert not outcome["ci_safe"] and not outcome["qualified"]
    state_case = next(row for row in outcome["ci_cases"] if row["case"] == "state-semantics")
    assert state_case["required"] == sorted(discovery.CHECKS)


def test_cross_stage_migration_checks_this_candidates_old_state_not_a_gold_schema(tmp_path):
    discovery.seed(tmp_path)
    discovery.synthetic_step(tmp_path, discovery.NAME, 1, False)
    previous = discovery.evaluate(tmp_path, discovery.NAME, 1, random_seed=44)["hidden"][
        "compatibility_fixture"
    ]
    discovery.synthetic_step(tmp_path, discovery.NAME, 2, False)
    source = tmp_path / "solution.py"
    source.write_text(
        source.read_text().replace(
            "booking.setdefault('cancelled', False)", "booking.setdefault('cancelled', True)"
        )
    )
    outcome = discovery.evaluate(
        tmp_path, discovery.NAME, 2, random_seed=44, previous_state=json.loads(json.dumps(previous))
    )
    assert not outcome["qualified"]
    assert any(
        row["check"] == "cross-stage-historical-state-and-identity"
        for row in outcome["hidden"]["failures"]
    )
