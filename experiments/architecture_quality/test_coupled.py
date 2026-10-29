"""The cross-cutting seed preserves behavior; its structure earns no score."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from . import coupled, discovery

_BASELINE_PROBE = """import copy, json
from solution import execute

observed = []
state, effects = {}, []
def provider(operation, payload):
    effects.append([operation, copy.deepcopy(payload)])
    payload['customer'] = 'mutated by provider'
    return True

def call(request, settings=None, selected=provider):
    global state
    request_before, settings_before = copy.deepcopy(request), copy.deepcopy(settings)
    try:
        value = execute(request, state, settings, selected)
        observed.append(['result', value])
    except Exception as error:
        observed.append(['error', type(error).__name__])
    assert request == request_before and settings == settings_before
    state = json.loads(json.dumps(state))

reservation = {'action':'reserve','request_id':'first','workshop':'ceramics','customer':'Ada'}
call(reservation)
call(reservation)
call(reservation | {'quantity':2})
call(reservation | {'customer':'different'})
call({'action':'summary'})
call({'action':'flush'}, selected=lambda *args: False)
call({'action':'flush'})
call({'action':'flush'})
for identity in range(12):
    call(reservation | {'request_id':str(identity)})
call({'action':'summary'})
for invalid in ({'action':'unknown'}, reservation | {'quantity':True}, reservation | {'workshop':'absent'}):
    call(invalid)
state = {}
for outcome in (False, OSError, RuntimeError):
    def failing(operation, payload):
        effects.append([operation, copy.deepcopy(payload)])
        if isinstance(outcome, type):
            raise outcome('failure')
        return outcome
    call(reservation, selected=failing)
    call({'action':'summary'})
call(reservation)
call({'action':'flush'})
call({'action':'summary'})
print(json.dumps({'observed':observed, 'effects':effects}, sort_keys=True))
"""


def _probe(root):
    result = subprocess.run(
        [sys.executable, "-c", _BASELINE_PROBE],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_initial_cross_cutting_seed_preserves_public_baseline_outputs_and_effects(tmp_path):
    original, candidate = tmp_path / "original", tmp_path / "candidate"
    discovery.seed(original)
    coupled.seed(candidate)
    observed = _probe(candidate)
    assert observed == _probe(original)
    first = {"ok": True, "reservation_id": "first", "quantity": 1, "amount_cents": 1275}
    assert observed["observed"][:4] == [
        ["result", first],
        ["result", first],
        ["error", "ValueError"],
        ["error", "ValueError"],
    ]
    assert observed["observed"][4][1]["rows"][0] == {
        "workshop": "ceramics",
        "local_date": "2030-06-15",
        "seats": 1,
        "gross_cents": 1275,
        "pending_messages": 1,
    }
    assert [entry[1] for entry in observed["observed"][5:8]] == [
        {"ok": True, "sent": 0, "pending": 1},
        {"ok": True, "sent": 1, "pending": 0},
        {"ok": True, "sent": 0, "pending": 0},
    ]
    assert ["result", {"ok": False, "error": "sold_out"}] in observed["observed"]
    for error in ("payment_declined", "payment_unavailable"):
        assert ["result", {"ok": False, "error": error}] in observed["observed"]
    assert ["error", "RuntimeError"] in observed["observed"]
    assert all(
        payload["customer"] == "Ada"
        for operation, payload in observed["effects"]
        if operation == "notify"
    )
    # These are source observations about the controlled intervention, not an
    # architectural score or a required future layout.
    assert {path.name for path in candidate.glob("*.py")} == {
        "solution.py",
        "records.py",
        "channels.py",
        "ci_select.py",
    }
    assert "'quantity': 1" in (candidate / "solution.py").read_text()
    assert "'quantity': 1" in (candidate / "channels.py").read_text()
    assert "'seats': len(bookings)" in (candidate / "records.py").read_text()


@pytest.mark.parametrize("modular", [False, True])
def test_existing_oracle_checks_all_stages_and_actual_initial_state(tmp_path, modular):
    coupled.seed(tmp_path)
    previous = coupled.capture_state(tmp_path)
    assert previous["response"]["quantity"] == 1
    for stage in range(4):
        coupled.synthetic_step(tmp_path, coupled.NAME, stage, modular)
        outcome = coupled.evaluate(
            tmp_path, coupled.NAME, stage, random_seed=81, previous_state=previous
        )
        assert outcome["qualified"], outcome.get("hidden", outcome)
        assert outcome["hidden"]["cross_stage_state_compatibility"] == "checked"
        previous = outcome["hidden"]["compatibility_fixture"]


def test_seed_and_shared_brief_do_not_disclose_later_tasks_or_a_target_design(tmp_path):
    coupled.seed(tmp_path)
    text = "\n".join(path.read_text() for path in tmp_path.rglob("*.py"))
    for future in ("FEATURE_STAGE", "_cancel", "refund", "authorization_id", "refunded"):
        assert future not in text
    assert coupled.brief() == discovery.brief()
    assert not (tmp_path / "components.json").exists()
    for stage in range(4):
        assert coupled.task(coupled.NAME, stage) == discovery.task(discovery.NAME, stage)
    with pytest.raises(ValueError):
        coupled.seed(tmp_path, "unknown")
