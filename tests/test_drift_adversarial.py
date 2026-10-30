"""Concrete regressions must fail the benchmark's independent safety probes."""

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def drift(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    return tuple(
        importlib.import_module(f"experiments.drift.{name}")
        for name in ("fixture", "oracle", "delivery")
    )


def seed(root, stage, fixture):
    spec = fixture.specification(stage, fixture.identities(8, 8))
    fixture.write(root, fixture.files(spec, coupled=False))
    return spec


def assert_passed(result):
    assert all(row["passed"] for row in result["checks"].values()), result
    assert result["unchanged"]


def test_failed_send_cannot_destroy_preexisting_receipts_and_pending_events(tmp_path, drift):
    fixture, oracle, _ = drift
    spec = seed(tmp_path, 5, fixture)
    check = "c:" + spec["ids"]["providers"][0]
    assert_passed(oracle.run(tmp_path, spec, [check]))
    source = tmp_path / "solution.py"
    original = source.read_text()
    marker = "if not accepted: return {'ok': False, 'error': 'unavailable'}"
    assert marker in original
    source.write_text(original.replace(marker, marker.replace("return", "state.clear(); return")))
    result = oracle.run(tmp_path, spec, [check])
    assert not result["checks"][check]["passed"]
    assert "existing receipts or events" in result["checks"][check]["message"]


def test_receipt_migration_cannot_discard_old_opaque_pending_events(tmp_path, drift):
    fixture, oracle, _ = drift
    previous = seed(tmp_path, 5, fixture)
    check = "c:" + previous["ids"]["providers"][0]
    before = oracle.run(tmp_path, previous, [check])
    assert_passed(before)
    current = seed(tmp_path, 6, fixture)
    assert_passed(oracle.run(tmp_path, current, [check], before["retained"]))
    source = tmp_path / "solution.py"
    original = source.read_text()
    marker = "        state['records'] = state.pop('receipts', state.get('records', {}))"
    assert marker in original
    source.write_text(
        original.replace(
            marker,
            "        if state.get('version') != 2:\n            state['events'] = []\n" + marker,
        )
    )
    result = oracle.run(tmp_path, current, [check], before["retained"])
    assert not result["checks"][check]["passed"]
    assert "Earlier pending events" in result["checks"][check]["message"]


def test_release_recovery_requires_complete_checks_of_exact_previous_source(tmp_path, drift):
    fixture, oracle, delivery = drift
    spec = seed(tmp_path, 23, fixture)
    runtime = {"python": "3.12"}
    previous = delivery.artifacts(tmp_path, spec, runtime)
    proposal = {"selected": ["gate"], "deploy": [], "unknowns": []}
    receipt = delivery.receipt(tmp_path, spec, proposal["selected"], runtime, previous)
    for recovery in (
        {"unchanged": True, "checks": {}, "candidate_digest": "previous-source"},
        {
            "unchanged": True,
            "checks": {key: {"passed": True} for key in oracle.checks(spec)},
            "candidate_digest": "different-source",
        },
    ):
        result = delivery.verify(
            tmp_path,
            spec,
            proposal,
            receipt,
            runtime,
            previous,
            recovery=recovery,
            recovery_digest="previous-source",
        )
        assert not result["passed"]
        assert "rollback_failed" in result["failures"]


def test_prose_and_check_policy_do_not_change_deployable_artifact_bytes(tmp_path, drift):
    fixture, _, delivery = drift
    spec = seed(tmp_path, 5, fixture)
    runtime = {"python": "3.12"}
    before = delivery.artifacts(tmp_path, spec, runtime)
    for relative in ("BRIEF.md", "contracts.md", "anaxigraph/ci-policy.json"):
        target = tmp_path / relative
        target.write_text(target.read_text() + "\n")
    assert delivery.artifacts(tmp_path, spec, runtime) == before
