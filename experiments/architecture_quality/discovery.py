"""A discovery scenario: product work without prescribed architectural seams.

The generated monolith and separated implementation are calibration fixtures, not
architectural ground truth. Qualification establishes behavior/CI safety only.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

NAME = "workshop"
SCENARIOS = (NAME,)
CHECKS = ("reservations", "notifications", "reports", "smoke")
SETTINGS = {
    "fee_cents": 75,
    "report_offset_hours": 0,
    "catalog": {
        "ceramics": {"price_cents": 1200, "capacity": 12, "starts_at": "2030-06-15T10:00:00+00:00"},
        "robotics": {"price_cents": 1800, "capacity": 8, "starts_at": "2030-06-16T00:30:00+00:00"},
    },
}


def brief(name=NAME):
    if name != NAME:
        raise ValueError(name)
    return """The workshop desk sells places and gives staff a current booking summary.
Keep its observable behavior while delivering the current feature. Keep the code understandable,
document important behavior, retain the existing checks, and provide trustworthy fast feedback.
Use stdlib only; no real payment or messaging services are needed.

Public API: solution.execute(request, state, settings=None, provider=None) returns a JSON dict.
state starts as {}, is modified in place after successful operations, and can be JSON-serialized
and restored between calls. Its internal representation is private. Different state dictionaries
must remain independent. Existing serialized states must remain usable after feature updates.
Do not mutate request/settings or expose aliases to stored responses.
settings defaults to settings.json plus the version in schema.json. Custom settings include
fee_cents (nonnegative integer), report_offset_hours (-23..23 integer), schema_version (positive integer),
and catalog: workshop IDs mapped to positive integer price_cents/capacity and aware ISO starts_at.
Booleans do not count as integers. Invalid requests/settings raise ValueError before any side
effect or state change. Unknown actions/workshops are invalid. Nonempty string IDs/customer
names are accepted, including Unicode. provider defaults to accepting and is a callable
provider(operation, payload). All monetary values are integer cents.

reserve takes request_id, workshop, customer. A successful single-place reservation costs
catalog price plus one booking fee. Call provider('charge', {request_id, workshop, customer,
amount_cents}) once. True succeeds; False returns {ok:false,error:'payment_declined'} and no
state change. OSError returns payment_unavailable, without state change. Other exceptions
propagate without state change. Success returns {ok:true,reservation_id:request_id,quantity:1,
amount_cents:paid_amount}, consumes one place and queues one confirmation. Capacity exhaustion
returns {ok:false,error:'sold_out'} without a provider call. Retrying a successful request_id
with the same workshop/customer/quantity returns the same response and never charges or queues
again, including after JSON state restoration. Reusing it with different values raises ValueError.
Failed attempts do not consume an identity or capacity. Charge success must survive a later
notification failure. Prices already paid are historical facts, not recomputed from current prices.

flush takes optional positive integer limit (default 100). It attempts up to limit pending
messages in creation order, calling provider('notify', payload). Payload contains event_id
'<reservation_id>/confirmed', kind 'confirmed', reservation_id, customer, workshop, quantity,
historically paid amount_cents, local_date and schema_version. local_date is starts_at shifted
by report_offset_hours, formatted YYYY-MM-DD. True marks that event sent; False/OSError retains
it for retry. Successful notifications are never resent. Unexpected errors propagate; prior
successful sends stay recorded. Return {ok:true,sent:<this call>,pending:<remaining count>}.
Retrying flush never charges. Build pending message payloads using the current reporting
offset/schema, while retaining the original paid amount and customer. Provider mutation of a
payload must not corrupt saved data.

summary has no side effects and returns {ok:true,schema_version,rows:[...]} for all workshops,
sorted by workshop ID. Each row contains workshop, local_date, seats, gross_cents and
pending_messages. Count successful bookings and their historical paid amounts; pending_messages
counts unsent events for that workshop. Current report offset/schema apply to every summary.

Existing product checks run as `python tests/test_<id>.py` for reservations, notifications,
reports and smoke. ci_select.py reads {changed_paths:[...],changed_contracts:[...]} on stdin and
returns a JSON list of retained check IDs. Keeping all checks is safe. You may improve selection
if you can justify its inputs. Always keep smoke; shared settings/schema/build/check-selection
changes, unknown inputs and 'state' contract changes require all checks. A 'provider' contract
change needs reservations, notifications and smoke. Check IDs describe product checks, not a
required source decomposition. There is no required component manifest or directory layout.
"""


def task(name, stage):
    if name != NAME:
        raise ValueError(name)
    if stage == 0:
        return (
            "Feature: support group reservations with quantity defaulting to 1, an integer from "
            "1 through 6. Reserve all requested places or none; charge price_cents * quantity "
            "+ one booking fee. The response/message quantity and summary seat counts must agree. "
            "A group that exceeds remaining capacity must cause no charge or state change. "
            "Keep existing single-place behavior and retry guarantees."
        )
    if stage == 1:
        return (
            "Provider update: preserve legacy boolean results, and also accept structured replies. "
            "For charge, status='authorized' succeeds and authorization_id is copied into the "
            "reservation response. status='declined' returns payment_declined; status='pending' "
            "returns payment_pending. Failed/pending attempts do not reserve capacity. For notify, "
            "status='sent' succeeds; other statuses retain the event for retry. Keep all existing "
            "OSError, idempotency, historical-price and payload isolation guarantees."
        )
    if stage == 2:
        return (
            "Feature: cancel a reservation using action='cancel', request_id and reservation_id. "
            "Cancellation identities are separate from reservation identities, so the same string "
            "can identify one of each. Refund the exact historical paid amount through "
            "provider('refund',{request_id,reservation_id,amount_cents,authorization_id}), where "
            "authorization_id is null for legacy payments. True or status='refunded' succeeds; "
            "False/declined returns refund_declined, pending returns refund_pending, OSError returns "
            "refund_unavailable. Those failures change no state/capacity. Other exceptions propagate. "
            "On success release all seats, remove its unsent confirmation, and queue exactly one "
            "kind='cancelled' message with event_id '<reservation_id>/cancelled'. Return "
            "{ok:true,reservation_id,status:'cancelled',amount_cents:<historical amount>}. Repeat "
            "identical cancellation IDs or cancel an already cancelled reservation without another "
            "refund/message. Conflicting cancellation identity reuse raises ValueError. Unknown "
            "reservations raise ValueError. Summaries exclude cancelled seats/revenue and add "
            "cancelled_seats to every row. Preserve state across JSON roundtrips and refund retries."
        )
    return (
        "Shared configuration rollout: settings.json fee_cents becomes 125, report_offset_hours "
        "becomes -5, and schema.json version becomes 2. Public function signatures stay unchanged. "
        "New bookings use the new fee; existing bookings/refunds retain their original paid amounts. "
        "Pending notification payloads and every summary use the new schema version and shifted "
        "calendar date. Previously serialized state remains usable. Retain the checks affected "
        "by these shared semantics, including when no imports or public signatures change."
    )


SUPPORT = """import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

FEATURE_STAGE = STAGE_VALUE

def _text(value):
    if not isinstance(value, str) or not value:
        raise ValueError('nonempty string')
    return value

def _integer(value, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError('integer range')
    return value

def _configuration(settings):
    if settings is None:
        root = Path(__file__).parent
        settings = json.loads((root / 'settings.json').read_text())
        settings['schema_version'] = json.loads((root / 'schema.json').read_text())['version']
    if not isinstance(settings, dict):
        raise ValueError('settings')
    result = copy.deepcopy(settings)
    _integer(result.get('fee_cents'))
    _integer(result.get('report_offset_hours'), -23, 23)
    _integer(result.get('schema_version'), 1, 2)
    if not isinstance(result.get('catalog'), dict) or not result['catalog']:
        raise ValueError('catalog')
    for identity, item in result['catalog'].items():
        _text(identity)
        if not isinstance(item, dict) or not isinstance(item.get('starts_at'), str):
            raise ValueError('workshop configuration')
        _integer(item.get('price_cents'), 1)
        _integer(item.get('capacity'), 1)
        if datetime.fromisoformat(item['starts_at']).utcoffset() is None:
            raise ValueError('aware start time')
    return result

def _state(state):
    result = copy.deepcopy(state)
    for key in ('bookings', 'cancellations', 'events'):
        result.setdefault(key, {})
    return result

def _commit(state, draft):
    state.clear()
    state.update(draft)

def _day(configuration, workshop):
    value = datetime.fromisoformat(configuration['catalog'][workshop]['starts_at'])
    return (value + timedelta(hours=configuration['report_offset_hours'])).date().isoformat()

def _reply(value, operation):
    if FEATURE_STAGE >= 1 and isinstance(value, dict):
        success = {'charge': 'authorized', 'notify': 'sent', 'refund': 'refunded'}[operation]
        return value.get('status') == success, value.get('status') == 'pending'
    return bool(value), False
"""

RESERVATIONS = """def _reserve(request, state, configuration, provider):
    identity, workshop, customer = (_text(request.get(key)) for key in ('request_id', 'workshop', 'customer'))
    quantity = _integer(request.get('quantity', 1), 1, 6 if FEATURE_STAGE >= 0 else 1)
    if workshop not in configuration['catalog']:
        raise ValueError('workshop')
    draft = _state(state)
    fingerprint = [workshop, customer, quantity]
    if identity in draft['bookings']:
        existing = draft['bookings'][identity]
        if existing['fingerprint'] != fingerprint:
            raise ValueError('conflicting request identity')
        return copy.deepcopy(existing['response'])
    occupied = sum(item['quantity'] for item in draft['bookings'].values() if item['workshop'] == workshop and not item.get('cancelled'))
    if occupied + quantity > configuration['catalog'][workshop]['capacity']:
        return {'ok': False, 'error': 'sold_out'}
    amount = configuration['catalog'][workshop]['price_cents'] * quantity + configuration['fee_cents']
    try:
        answer = provider('charge', {'request_id': identity, 'workshop': workshop, 'customer': customer, 'amount_cents': amount})
    except OSError:
        return {'ok': False, 'error': 'payment_unavailable'}
    accepted, pending = _reply(answer, 'charge')
    if not accepted:
        return {'ok': False, 'error': 'payment_pending' if pending else 'payment_declined'}
    response = {'ok': True, 'reservation_id': identity, 'quantity': quantity, 'amount_cents': amount}
    authorization = answer.get('authorization_id') if FEATURE_STAGE >= 1 and isinstance(answer, dict) else None
    if authorization is not None:
        response['authorization_id'] = authorization
    record = {'fingerprint': fingerprint, 'workshop': workshop, 'customer': customer, 'quantity': quantity, 'amount_cents': amount, 'authorization_id': authorization, 'response': copy.deepcopy(response), 'cancelled': False}
    draft['bookings'][identity] = record
    draft['events'][identity + '/confirmed'] = {'reservation_id': identity, 'kind': 'confirmed', 'sent': False}
    _commit(state, draft)
    return response

def _cancel(request, state, configuration, provider):
    if FEATURE_STAGE < 2:
        raise ValueError('action')
    identity, reservation = (_text(request.get(key)) for key in ('request_id', 'reservation_id'))
    draft = _state(state)
    if identity in draft['cancellations']:
        previous = draft['cancellations'][identity]
        if previous['reservation_id'] != reservation:
            raise ValueError('conflicting cancellation identity')
        return copy.deepcopy(previous['response'])
    if reservation not in draft['bookings']:
        raise ValueError('reservation')
    record = draft['bookings'][reservation]
    response = {'ok': True, 'reservation_id': reservation, 'status': 'cancelled', 'amount_cents': record['amount_cents']}
    if not record['cancelled']:
        payload = {'request_id': identity, 'reservation_id': reservation, 'amount_cents': record['amount_cents'], 'authorization_id': record.get('authorization_id')}
        try:
            answer = provider('refund', payload)
        except OSError:
            return {'ok': False, 'error': 'refund_unavailable'}
        accepted, pending = _reply(answer, 'refund')
        if not accepted:
            return {'ok': False, 'error': 'refund_pending' if pending else 'refund_declined'}
        record['cancelled'] = True
        old_event = draft['events'].get(reservation + '/confirmed')
        if old_event and not old_event['sent']:
            del draft['events'][reservation + '/confirmed']
        draft['events'].setdefault(reservation + '/cancelled', {'reservation_id': reservation, 'kind': 'cancelled', 'sent': False})
    draft['cancellations'][identity] = {'reservation_id': reservation, 'response': copy.deepcopy(response)}
    _commit(state, draft)
    return response
"""

NOTIFICATIONS = """def _flush(request, state, configuration, provider):
    limit = _integer(request.get('limit', 100), 1)
    sent, attempted = 0, 0
    draft = _state(state)
    for event_id, event in draft['events'].items():
        if event['sent'] or attempted >= limit:
            continue
        attempted += 1
        booking = draft['bookings'][event['reservation_id']]
        payload = {'event_id': event_id, 'kind': event['kind'], 'reservation_id': event['reservation_id'], 'customer': booking['customer'], 'workshop': booking['workshop'], 'quantity': booking['quantity'], 'amount_cents': booking['amount_cents'], 'local_date': _day(configuration, booking['workshop']), 'schema_version': configuration['schema_version']}
        try:
            answer = provider('notify', copy.deepcopy(payload))
        except OSError:
            continue
        accepted, _ = _reply(answer, 'notify')
        if accepted:
            event['sent'] = True
            sent += 1
            _commit(state, copy.deepcopy(draft))
    _commit(state, draft)
    return {'ok': True, 'sent': sent, 'pending': sum(not item['sent'] for item in draft['events'].values())}
"""

REPORTS = """def _summary(request, state, configuration, provider):
    draft, rows = _state(state), []
    for workshop in sorted(configuration['catalog']):
        bookings = [item for item in draft['bookings'].values() if item['workshop'] == workshop]
        row = {'workshop': workshop, 'local_date': _day(configuration, workshop), 'seats': sum(item['quantity'] for item in bookings if not item['cancelled']), 'gross_cents': sum(item['amount_cents'] for item in bookings if not item['cancelled']), 'pending_messages': sum(not event['sent'] and draft['bookings'][event['reservation_id']]['workshop'] == workshop for event in draft['events'].values())}
        if FEATURE_STAGE >= 2:
            row['cancelled_seats'] = sum(item['quantity'] for item in bookings if item['cancelled'])
        rows.append(row)
    return {'ok': True, 'schema_version': configuration['schema_version'], 'rows': rows}
"""

FACADE = """def execute(request, state, settings=None, provider=None):
    configuration = _configuration(settings)
    if not isinstance(request, dict) or not isinstance(state, dict):
        raise ValueError('request/state')
    if provider is None:
        provider = lambda operation, payload: True
    action = request.get('action')
    functions = {'reserve': _reserve, 'summary': _summary, 'flush': _flush, 'cancel': _cancel}
    if not isinstance(action, str) or action not in functions:
        raise ValueError('action')
    return functions[action](request, state, configuration, provider)
"""


def _source(root: Path, stage: int, modular: bool):
    support = SUPPORT.replace("FEATURE_STAGE = STAGE_VALUE\n", "")
    support = support.replace(
        "_integer(result.get('schema_version'), 1, 2)", "_integer(result.get('schema_version'), 1)"
    )
    reservations, reports, facade = RESERVATIONS, REPORTS, FACADE
    reservations = reservations.replace(
        "6 if FEATURE_STAGE >= 0 else 1", str(6 if stage >= 0 else 1)
    )
    if stage < 1:
        support = (
            support[: support.index("def _reply(value, operation):")]
            + "def _reply(value, operation):\n    return bool(value), False\n"
        )
        reservations = reservations.replace(
            "    authorization = answer.get('authorization_id') if FEATURE_STAGE >= 1 and isinstance(answer, dict) else None\n    if authorization is not None:\n        response['authorization_id'] = authorization\n",
            "",
        )
        reservations = reservations.replace("'authorization_id': authorization, ", "")
    else:
        support = support.replace("FEATURE_STAGE >= 1 and ", "")
        if stage < 2:
            support = support.replace(", 'refund': 'refunded'", "")
        reservations = reservations.replace("FEATURE_STAGE >= 1 and ", "")
    if stage < 2:
        support = support.replace(
            "('bookings', 'cancellations', 'events')", "('bookings', 'events')"
        )
        reservations = reservations[: reservations.index("\ndef _cancel(")]
        reservations = reservations.replace(" and not item.get('cancelled')", "").replace(
            ", 'cancelled': False", ""
        )
        reports = reports.replace(" if not item['cancelled']", "")
        start = reports.index("        if FEATURE_STAGE >= 2:")
        end = reports.index("        rows.append(row)", start)
        reports = reports[:start] + reports[end:]
        facade = facade.replace(", 'cancel': _cancel", "")
    else:
        reservations = reservations.replace(
            "    if FEATURE_STAGE < 2:\n        raise ValueError('action')\n", ""
        )
        support = support.replace(
            "        result.setdefault(key, {})\n    return result",
            "        result.setdefault(key, {})\n    for booking in result['bookings'].values():\n        booking.setdefault('cancelled', False)\n    return result",
        )
        reports = reports.replace("        if FEATURE_STAGE >= 2:\n            ", "        ")
    helpers = "copy, _text, _integer, _state, _commit, _day, _reply"
    if modular:
        imports = "_reserve, _cancel" if stage >= 2 else "_reserve"
        files = {
            "support.py": support,
            "orders.py": "from support import " + helpers + "\n\n" + reservations,
            "notices.py": "from support import " + helpers + "\n\n" + NOTIFICATIONS,
            "overview.py": "from support import " + helpers + "\n\n" + reports,
            "solution.py": "from support import _configuration\nfrom orders import "
            + imports
            + "\nfrom notices import _flush\nfrom overview import _summary\n\n"
            + facade,
        }
    else:
        files = {
            "solution.py": "\n\n".join((support, reservations, NOTIFICATIONS, reports, facade))
        }
    for name in ("solution.py", "support.py", "orders.py", "notices.py", "overview.py"):
        (root / name).unlink(missing_ok=True)
    for name, source in files.items():
        (root / name).write_text(source)


def seed(root: Path, name=NAME, *, modular=False, stage=-1):
    if name != NAME:
        raise ValueError(name)
    root.mkdir(parents=True, exist_ok=True)
    _source(root, stage, modular)
    (root / "settings.json").write_text(json.dumps(SETTINGS, indent=2))
    (root / "schema.json").write_text('{"version": 1}\n')
    (root / "BRIEF.md").write_text(brief())
    (root / "contracts.md").write_text(brief())
    (root / "pyproject.toml").write_text('[project]\nname="workshop-desk"\nversion="0.0.0"\n')
    (root / "ci_select.py").write_text(
        "import json\nprint(json.dumps(" + repr(list(CHECKS)) + "))\n"
    )
    (root / "tests").mkdir(exist_ok=True)
    examples = {
        "reservations": "self.assertTrue(execute({'action':'reserve','request_id':'one','workshop':'ceramics','customer':'Ada'}, state)['ok'])",
        "notifications": "execute({'action':'reserve','request_id':'one','workshop':'ceramics','customer':'Ada'}, state)\n        self.assertEqual(execute({'action':'flush'}, state)['sent'], 1)",
        "reports": "self.assertEqual(execute({'action':'summary'}, state)['rows'][0]['seats'], 0)",
        "smoke": "self.assertTrue(execute({'action':'summary'}, state)['ok'])",
    }
    for check, example in examples.items():
        (root / "tests" / f"test_{check}.py").write_text(
            "import sys, unittest\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parents[1]))\nfrom solution import execute\n\nclass ProductBehavior(unittest.TestCase):\n    def test_example(self):\n        state = {}\n        "
            + example
            + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
        )


def synthetic_step(root: Path, name: str, stage: int, treatment: bool):
    """Automation calibration, not generated advice or model effectiveness evidence."""
    if name != NAME:
        raise ValueError(name)
    _source(root, stage, modular=treatment)
    if stage >= 3:
        settings = copy.deepcopy(SETTINGS)
        settings.update(fee_cents=125, report_offset_hours=-5)
        (root / "settings.json").write_text(json.dumps(settings, indent=2))
        (root / "schema.json").write_text('{"version": 2}\n')


class Reference:
    """Independent state model; never copied/imported into a candidate checkout."""

    def __init__(self, stage):
        self.stage, self.bookings, self.cancellations, self.events = stage, {}, {}, []

    def summary(self, configuration):
        rows = []
        for workshop, item in sorted(configuration["catalog"].items()):
            relevant = [entry for entry in self.bookings.values() if entry["workshop"] == workshop]
            row = {
                "workshop": workshop,
                "local_date": (
                    datetime.fromisoformat(item["starts_at"])
                    + timedelta(hours=configuration["report_offset_hours"])
                )
                .date()
                .isoformat(),
                "seats": sum(entry["quantity"] for entry in relevant if not entry["cancelled"]),
                "gross_cents": sum(entry["amount"] for entry in relevant if not entry["cancelled"]),
                "pending_messages": sum(
                    not event["sent"]
                    and self.bookings[event["reservation"]]["workshop"] == workshop
                    for event in self.events
                ),
            }
            if self.stage >= 2:
                row["cancelled_seats"] = sum(
                    entry["quantity"] for entry in relevant if entry["cancelled"]
                )
            rows.append(row)
        return {"ok": True, "schema_version": configuration["schema_version"], "rows": rows}


def _compatibility_fixture(execute, root, quantity, structured=False):
    settings = json.loads((root / "settings.json").read_text())
    settings["schema_version"] = json.loads((root / "schema.json").read_text())["version"]
    request = {
        "action": "reserve",
        "request_id": "compatibility/original",
        "workshop": "robotics",
        "customer": "Historical customer",
        "quantity": quantity,
    }
    state = {}
    reply = (
        {"status": "authorized", "authorization_id": "historical-authorization"}
        if structured
        else True
    )
    response = execute(request, state, settings, lambda *args: reply)
    expected = {
        "ok": True,
        "reservation_id": request["request_id"],
        "quantity": quantity,
        "amount_cents": settings["catalog"]["robotics"]["price_cents"] * quantity
        + settings["fee_cents"],
    }
    if structured:
        expected["authorization_id"] = "historical-authorization"
    assert response == expected
    return {
        "state": json.loads(json.dumps(state)),
        "request": request,
        "response": response,
        "settings": settings,
    }


def capture_state(root: Path):
    """Capture only public baseline behavior; return opaque state to the private coordinator."""
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--capture-state",
            str(root.resolve()),
            "-1",
            "0",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout.splitlines()[-1]) if result.returncode == 0 else None


def behavior(root: Path, stage: int, random_seed: int, previous_state=None):
    # Always execute current source, including same-size edits inside one clock tick.
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(
        root.parent / (".external-evaluator-cache-" + str(time.monotonic_ns()))
    )
    sys.path.insert(0, str(root))
    app = importlib.import_module("solution")
    rng, failures, checked, traces = (
        random.Random(random_seed),
        [],
        0,
        {key: set() for key in CHECKS},
    )

    def call(request, state, settings, provider):
        facet = (
            {
                "reserve": "reservations",
                "cancel": "reservations",
                "flush": "notifications",
                "summary": "reports",
            }.get(request.get("action"), "smoke")
            if isinstance(request, dict) and isinstance(request.get("action"), str)
            else "smoke"
        )
        before = copy.deepcopy((request, settings))

        def trace(frame, event, arg):
            prefix, filename = str(root) + "/", frame.f_code.co_filename
            if filename.startswith(prefix):
                if event == "call" and frame.f_code.co_name != "<module>":
                    traces[facet].add(filename.removeprefix(prefix))
                return trace
            return None

        sys.settrace(trace)
        try:
            result = app.execute(request, state, settings, provider)
        finally:
            sys.settrace(None)
            assert (request, settings) == before, "request/settings mutation"
        return result

    def check(label, function):
        nonlocal checked
        checked += 1
        try:
            function()
        except Exception as error:
            failures.append(
                {"check": label, "error": type(error).__name__, "detail": str(error)[:180]}
            )

    def configuration():
        value = copy.deepcopy(SETTINGS)
        value["schema_version"] = 1
        return value

    def scenario(index):
        config, state, reference, effects = configuration(), {}, Reference(stage), []
        config["catalog"]["ceramics"]["capacity"] = rng.randrange(7, 15)
        config["catalog"]["ceramics"]["price_cents"] = rng.randrange(1, 5000)
        config["fee_cents"] = rng.randrange(0, 300)
        outcomes = {}

        def provider(operation, payload):
            effects.append((operation, copy.deepcopy(payload)))
            value = outcomes.get(operation, True)
            if isinstance(value, type) and issubclass(value, Exception):
                raise value("injected")
            payload["customer"] = "provider must not mutate persisted customer"
            return value

        def verify_summary():
            original = copy.deepcopy(state)
            assert call({"action": "summary"}, state, config, provider) == reference.summary(config)
            assert state == original, "summary changed state"

        def reserve(identity, quantity, answer=True):
            request = {
                "action": "reserve",
                "request_id": identity,
                "workshop": "ceramics",
                "customer": "Åda " + str(index),
                "quantity": quantity,
            }
            outcomes["charge"] = answer
            before, start = copy.deepcopy(state), len(effects)
            result = call(request, state, config, provider)
            amount = config["catalog"]["ceramics"]["price_cents"] * quantity + config["fee_cents"]
            occupied = sum(
                item["quantity"] for item in reference.bookings.values() if not item["cancelled"]
            )
            if identity in reference.bookings:
                assert result == reference.bookings[identity]["response"] and len(effects) == start
            elif occupied + quantity > config["catalog"]["ceramics"]["capacity"]:
                assert (
                    result == {"ok": False, "error": "sold_out"}
                    and state == before
                    and len(effects) == start
                )
            else:
                assert effects[start:] == [
                    (
                        "charge",
                        {
                            "request_id": identity,
                            "workshop": "ceramics",
                            "customer": request["customer"],
                            "amount_cents": amount,
                        },
                    )
                ]
                accepted = (
                    answer.get("status") == "authorized"
                    if isinstance(answer, dict)
                    else answer is True
                )
                if accepted:
                    expected = {
                        "ok": True,
                        "reservation_id": identity,
                        "quantity": quantity,
                        "amount_cents": amount,
                    }
                    if isinstance(answer, dict):
                        expected["authorization_id"] = answer["authorization_id"]
                    assert result == expected
                    reference.bookings[identity] = {
                        "workshop": "ceramics",
                        "customer": request["customer"],
                        "quantity": quantity,
                        "amount": amount,
                        "authorization": answer.get("authorization_id")
                        if isinstance(answer, dict)
                        else None,
                        "response": expected,
                        "cancelled": False,
                    }
                    reference.events.append(
                        {"reservation": identity, "kind": "confirmed", "sent": False}
                    )
                else:
                    reason = (
                        "unavailable"
                        if answer is OSError
                        else "pending"
                        if isinstance(answer, dict) and answer.get("status") == "pending"
                        else "declined"
                    )
                    assert result == {"ok": False, "error": "payment_" + reason} and state == before
            verify_summary()
            return request

        def flush(answer, limit=100):
            outcomes["notify"] = answer
            start, sent, attempted = len(effects), 0, 0
            for event in reference.events:
                if event["sent"] or attempted >= limit:
                    continue
                attempted += 1
                if answer is True or isinstance(answer, dict) and answer.get("status") == "sent":
                    event["sent"] = True
                    sent += 1
            result = call({"action": "flush", "limit": limit}, state, config, provider)
            assert result == {
                "ok": True,
                "sent": sent,
                "pending": sum(not item["sent"] for item in reference.events),
            }
            assert all(operation == "notify" for operation, _ in effects[start:]), (
                "flush charged/refunded"
            )
            assert len(effects) - start == attempted
            for _, payload in effects[start:]:
                booked = reference.bookings[payload["reservation_id"]]
                event = next(
                    entry
                    for entry in reference.events
                    if entry["reservation"] == payload["reservation_id"]
                    and entry["kind"] == payload["kind"]
                )
                expected = {
                    "event_id": event["reservation"] + "/" + event["kind"],
                    "kind": event["kind"],
                    "reservation_id": event["reservation"],
                    "customer": booked["customer"],
                    "workshop": booked["workshop"],
                    "quantity": booked["quantity"],
                    "amount_cents": booked["amount"],
                    "schema_version": config["schema_version"],
                    "local_date": next(
                        row["local_date"]
                        for row in reference.summary(config)["rows"]
                        if row["workshop"] == booked["workshop"]
                    ),
                }
                assert payload == expected, "notification contract/historical value"
            verify_summary()

        first = reserve("r-" + str(index), rng.randrange(1, 7))
        state = json.loads(json.dumps(state))
        reserve(first["request_id"], first["quantity"])
        start, before = len(effects), copy.deepcopy(state)
        try:
            call(first | {"customer": "different"}, state, config, provider)
        except ValueError:
            pass
        else:
            raise AssertionError("conflicting identity accepted")
        assert state == before and len(effects) == start
        reserve("sold-out", 6)
        reserve("bad-payment", 1, False)
        reserve("bad-payment", 1, OSError)
        if stage >= 1:
            reserve("pending", 1, {"status": "pending"})
            reserve("declined", 1, {"status": "declined"})
            reserve(
                "authorized", 1, {"status": "authorized", "authorization_id": "A-" + str(index)}
            )
        flush(OSError)
        state = json.loads(json.dumps(state))
        flush(False, 1)
        flush({"status": "sent"} if stage >= 1 else True, 1)
        if stage >= 2:
            booking = reference.bookings[first["request_id"]]
            config["catalog"]["ceramics"]["price_cents"] += 9999
            cancel = {
                "action": "cancel",
                "request_id": first["request_id"],
                "reservation_id": first["request_id"],
            }
            for answer, error in (
                (False, "refund_declined"),
                (OSError, "refund_unavailable"),
                ({"status": "pending"}, "refund_pending"),
            ):
                outcomes["refund"] = answer
                before = copy.deepcopy(state)
                assert call(cancel, state, config, provider) == {"ok": False, "error": error}
                assert state == before
            outcomes["refund"] = {"status": "refunded", "refund_id": "refund-1"}
            start = len(effects)
            result = call(cancel, state, config, provider)
            assert effects[start:] == [
                (
                    "refund",
                    {
                        "request_id": cancel["request_id"],
                        "reservation_id": cancel["reservation_id"],
                        "amount_cents": booking["amount"],
                        "authorization_id": booking["authorization"],
                    },
                )
            ]
            expected = {
                "ok": True,
                "reservation_id": cancel["reservation_id"],
                "status": "cancelled",
                "amount_cents": booking["amount"],
            }
            assert result == expected
            booking["cancelled"] = True
            reference.events = [
                event
                for event in reference.events
                if event["reservation"] != cancel["reservation_id"] or event["sent"]
            ]
            reference.events.append(
                {"reservation": cancel["reservation_id"], "kind": "cancelled", "sent": False}
            )
            state = json.loads(json.dumps(state))
            start = len(effects)
            assert call(cancel, state, config, provider) == expected
            assert (
                call(cancel | {"request_id": "other-cancel"}, state, config, provider) == expected
            )
            assert len(effects) == start, "repeat cancellation refunded twice"
            verify_summary()
            reserve("after-cancel", first["quantity"])
        if stage >= 3:
            config.update(fee_cents=125, report_offset_hours=-5, schema_version=2)
            state = json.loads(json.dumps(state))
            verify_summary()
        flush(True)
        flush(True)
        before = copy.deepcopy(state)
        for invalid in (0, -1, True, 1.5, None):
            start = len(effects)
            try:
                call({"action": "flush", "limit": invalid}, state, config, provider)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid flush limit accepted")
            assert state == before and len(effects) == start

    for index in range(12):
        check("sequence-" + str(index), lambda i=index: scenario(i))

    def invalid_and_isolated():
        config, state, calls = configuration(), {}, []

        def provider(*args):
            calls.append(args)
            return True

        for quantity in (0, -1, True, 7, 1.5, None):
            request = {
                "action": "reserve",
                "request_id": "invalid",
                "workshop": "ceramics",
                "customer": "Ada",
                "quantity": quantity,
            }
            try:
                call(request, state, config, provider)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid group accepted")
            assert not state and not calls
        valid = {
            "action": "reserve",
            "request_id": "one",
            "workshop": "robotics",
            "customer": "Ada",
            "quantity": 2,
        }
        result = call(valid, state, config, provider)
        result["quantity"] = 99
        assert call(valid, state, config, provider)["quantity"] == 2, (
            "response aliases persisted state"
        )
        separate = {}
        assert call({"action": "summary"}, separate, config, provider)["rows"][1]["seats"] == 0
        assert not separate

    check("invalid-atomicity-state-isolation", invalid_and_isolated)

    def malformed_inputs():
        request = {
            "action": "reserve",
            "request_id": "invalid",
            "workshop": "ceramics",
            "customer": "Ada",
            "quantity": 2,
        }
        cases = [
            (request | {key: invalid}, configuration())
            for key in ("request_id", "customer", "workshop")
            for invalid in (None, "")
        ]
        cases += [
            (request | {"workshop": "missing"}, configuration()),
            ({"action": []}, configuration()),
            ([], configuration()),
        ]
        for field, invalid in (
            ("fee_cents", -1),
            ("fee_cents", True),
            ("report_offset_hours", 24),
            ("schema_version", 0),
        ):
            cases.append((request, configuration() | {field: invalid}))
        for field, invalid in (
            ("capacity", 0),
            ("price_cents", True),
            ("starts_at", "not-a-time"),
            ("starts_at", "2030-06-15T10:00:00"),
        ):
            settings = configuration()
            settings["catalog"]["ceramics"][field] = invalid
            cases.append((request, settings))
        for candidate, settings in cases:
            state, effects = {}, []
            try:
                call(candidate, state, settings, lambda *args: effects.append(args) or True)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid input/configuration accepted")
            assert not state and not effects, "validation occurred after effects/state change"

    check("malformed-public-inputs-and-configuration-before-effects", malformed_inputs)

    def provider_failures_are_atomic():
        config = configuration()
        replies = [(False, "payment_declined"), (OSError, "payment_unavailable")]
        if stage >= 1:
            replies += [
                ({"status": "pending"}, "payment_pending"),
                ({"status": "declined"}, "payment_declined"),
            ]
        for answer, error in replies:
            state, effects = {}, []

            def provider(operation, payload):
                effects.append((operation, copy.deepcopy(payload)))
                if answer is OSError:
                    raise OSError("unavailable")
                return answer

            request = {
                "action": "reserve",
                "request_id": "retry",
                "workshop": "robotics",
                "customer": "Ada",
                "quantity": 2,
            }
            assert call(request, state, config, provider) == {"ok": False, "error": error}
            assert not state and len(effects) == 1, "failed payment mutated state"
            assert call(request, state, config, lambda *args: True)["ok"]

        def unexpected(*args):
            raise RuntimeError("unexpected provider error")

        state = {}
        try:
            call(request, state, config, unexpected)
        except RuntimeError:
            pass
        else:
            raise AssertionError("unexpected charge failure swallowed")
        assert not state

    check("independent-provider-results-and-failure-atomicity", provider_failures_are_atomic)

    def mixed_workshops():
        config, state, messages = configuration(), {}, []
        for identity, workshop, quantity in (("ceramic", "ceramics", 3), ("robot", "robotics", 2)):
            request = {
                "action": "reserve",
                "request_id": identity,
                "workshop": workshop,
                "customer": identity,
                "quantity": quantity,
            }
            assert call(request, state, config, lambda *args: True)["ok"]
        result = call(
            {"action": "flush"},
            state,
            config,
            lambda operation, payload: messages.append(payload) or True,
        )
        assert result == {"ok": True, "sent": 2, "pending": 0}
        assert {item["workshop"]: item["local_date"] for item in messages} == {
            "ceramics": "2030-06-15",
            "robotics": "2030-06-16",
        }
        rows = call({"action": "summary"}, state, config, lambda *args: True)["rows"]
        assert [(row["workshop"], row["seats"], row["gross_cents"]) for row in rows] == [
            ("ceramics", 3, 3675),
            ("robotics", 2, 3675),
        ]

    check("mixed-workshop-state-pricing-and-calendar-isolation", mixed_workshops)

    if stage >= 2:

        def cancellation_identity_conflict():
            settings, state, effects = configuration(), {}, []

            def provider(operation, payload):
                effects.append((operation, copy.deepcopy(payload)))
                return (
                    {
                        "status": "authorized",
                        "authorization_id": "authorization/" + payload["request_id"],
                    }
                    if operation == "charge"
                    else True
                )

            for reservation in ("first", "second"):
                call(
                    {
                        "action": "reserve",
                        "request_id": reservation,
                        "workshop": "ceramics",
                        "customer": reservation,
                        "quantity": 2,
                    },
                    state,
                    settings,
                    provider,
                )
            cancel = {
                "action": "cancel",
                "request_id": "one-cancellation",
                "reservation_id": "first",
            }
            assert call(cancel, state, settings, provider)["ok"]
            assert effects[-1] == (
                "refund",
                {
                    "request_id": "one-cancellation",
                    "reservation_id": "first",
                    "amount_cents": 2475,
                    "authorization_id": "authorization/first",
                },
            )
            before, start = copy.deepcopy(state), len(effects)
            for invalid in (
                cancel | {"reservation_id": "second"},
                cancel | {"request_id": "missing", "reservation_id": "unknown"},
            ):
                try:
                    call(invalid, state, settings, provider)
                except ValueError:
                    pass
                else:
                    raise AssertionError("conflicting/unknown cancellation accepted")
                assert state == before and len(effects) == start

        check(
            "cancellation-operation-identity-and-conflict-atomicity", cancellation_identity_conflict
        )

    def partial_notifications():
        config, state = configuration(), {}
        for identity in ("first", "second"):
            call(
                {
                    "action": "reserve",
                    "request_id": identity,
                    "workshop": "robotics",
                    "customer": identity,
                },
                state,
                config,
                lambda *args: True,
            )
        sent = []

        def failing(operation, payload):
            sent.append(payload["reservation_id"])
            if len(sent) == 2:
                raise RuntimeError("unexpected second notification failure")
            return True

        try:
            call({"action": "flush"}, state, config, failing)
        except RuntimeError:
            pass
        else:
            raise AssertionError("unexpected failure swallowed")
        state = json.loads(json.dumps(state))
        retried = []
        result = call(
            {"action": "flush"},
            state,
            config,
            lambda operation, payload: retried.append(payload["reservation_id"]) or True,
        )
        assert result == {"ok": True, "sent": 1, "pending": 0}
        assert sent == ["first", "second"] and retried == ["second"], (
            "successful notification repeated"
        )

    check("partial-notification-progress-and-retry", partial_notifications)

    def defaults():
        settings = json.loads((root / "settings.json").read_text())
        schema = json.loads((root / "schema.json").read_text())
        expected = copy.deepcopy(SETTINGS)
        if stage >= 3:
            expected.update(fee_cents=125, report_offset_hours=-5)
        assert settings == expected and schema == {"version": 2 if stage >= 3 else 1}
        settings["schema_version"] = schema["version"]
        state, effects = {}, []
        request = {
            "action": "reserve",
            "request_id": "default",
            "workshop": "robotics",
            "customer": "Ada",
            "quantity": 2,
        }
        result = call(
            request,
            state,
            None,
            lambda operation, payload: effects.append((operation, payload)) or True,
        )
        assert result["amount_cents"] == 3600 + settings["fee_cents"]
        summary = call({"action": "summary"}, state, None, lambda *args: True)
        assert summary["schema_version"] == settings["schema_version"]
        assert summary["rows"][1]["local_date"] == ("2030-06-15" if stage >= 3 else "2030-06-16")

    check("default-shared-fee-schema-calendar-semantics", defaults)
    if previous_state is not None:

        def historical_state():
            state = copy.deepcopy(previous_state["state"])
            request, expected = previous_state["request"], previous_state["response"]
            settings = json.loads((root / "settings.json").read_text())
            settings["schema_version"] = json.loads((root / "schema.json").read_text())["version"]
            effects = []

            def provider(operation, payload):
                effects.append((operation, copy.deepcopy(payload)))
                return True

            assert call(request, state, settings, provider) == expected
            assert not effects, "old successful reservation charged again after an update"
            reference = Reference(stage)
            reference.bookings[request["request_id"]] = {
                "workshop": request["workshop"],
                "customer": request["customer"],
                "quantity": expected["quantity"],
                "amount": expected["amount_cents"],
                "cancelled": False,
            }
            reference.events.append(
                {"reservation": request["request_id"], "kind": "confirmed", "sent": False}
            )
            assert call({"action": "summary"}, state, settings, provider) == reference.summary(
                settings
            )
            if stage >= 3:
                delivery_state = copy.deepcopy(state)
                assert call({"action": "flush"}, delivery_state, settings, provider) == {
                    "ok": True,
                    "sent": 1,
                    "pending": 0,
                }
                expected_day = next(
                    row["local_date"]
                    for row in reference.summary(settings)["rows"]
                    if row["workshop"] == request["workshop"]
                )
                assert effects == [
                    (
                        "notify",
                        {
                            "event_id": request["request_id"] + "/confirmed",
                            "kind": "confirmed",
                            "reservation_id": request["request_id"],
                            "customer": request["customer"],
                            "workshop": request["workshop"],
                            "quantity": expected["quantity"],
                            "amount_cents": expected["amount_cents"],
                            "local_date": expected_day,
                            "schema_version": settings["schema_version"],
                        },
                    )
                ]
                effects.clear()
            if stage >= 2:
                cancel = {
                    "action": "cancel",
                    "request_id": request["request_id"],
                    "reservation_id": request["request_id"],
                }
                response = call(cancel, state, settings, provider)
                assert response["amount_cents"] == expected["amount_cents"]
                assert effects == [
                    (
                        "refund",
                        {
                            "request_id": cancel["request_id"],
                            "reservation_id": request["request_id"],
                            "amount_cents": expected["amount_cents"],
                            "authorization_id": expected.get("authorization_id"),
                        },
                    )
                ]
                state = json.loads(json.dumps(state))
                assert call(cancel, state, settings, provider) == response
                assert len(effects) == 1, "old cancellation refunded again after restart"

        check("cross-stage-historical-state-and-identity", historical_state)
    compatibility = None

    def export_state():
        nonlocal compatibility
        compatibility = _compatibility_fixture(call, root, 2, structured=stage >= 1)

    check("capture-public-state-for-next-stage", export_state)
    return {
        "checks": checked,
        "failures": failures,
        "executed_inputs": {key: sorted(paths) for key, paths in traces.items()},
        "compatibility_fixture": compatibility,
        "cross_stage_state_compatibility": "checked"
        if previous_state is not None
        else "unmeasured",
        "observation_limit": "Executed function inputs are a lower bound; not a complete dependency graph or architecture score.",
    }


def evaluate(
    root: Path, name=NAME, stage=0, *, random_seed: int, changed_paths=(), previous_state=None
):
    if name != NAME:
        raise ValueError(name)
    root, started = root.resolve(), time.monotonic()

    def fingerprint():
        return {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file()
            and not any(
                part.startswith(".") or part == "__pycache__"
                for part in path.relative_to(root).parts
            )
            and str(path.relative_to(root)) != "anaxigraph/record.json"
        }

    before = fingerprint()
    try:
        worker = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
                str(root),
                str(stage),
                str(random_seed),
            ],
            input=json.dumps(previous_state),
            capture_output=True,
            text=True,
            timeout=60,
        )
        hidden = json.loads(worker.stdout.splitlines()[-1]) if worker.returncode == 0 else {}
    except (ValueError, IndexError, subprocess.TimeoutExpired):
        hidden = {}
    if not hidden:
        return {
            "qualified": False,
            "behavior_passed": False,
            "error": "discovery behavioral evaluator failed",
        }
    checks = {}
    for check in CHECKS:
        check_started = time.monotonic()
        try:
            result = subprocess.run(
                [sys.executable, str(root / "tests" / f"test_{check}.py")],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout + result.stderr
            checks[check] = {
                "passed": result.returncode == 0
                and "Ran " in output
                and "Ran 0 tests" not in output,
                "elapsed_seconds": time.monotonic() - check_started,
                "output": output[-1200:],
            }
        except subprocess.TimeoutExpired:
            checks[check] = {
                "passed": False,
                "elapsed_seconds": time.monotonic() - check_started,
                "error": "timeout",
            }
    execution_inputs = hidden["executed_inputs"]
    cases = [
        ("shared-or-unknown", [path], [], set(CHECKS))
        for path in (
            "settings.json",
            "schema.json",
            "pyproject.toml",
            "ci_select.py",
            "ci_policy.json",
            "unknown.data",
        )
    ]
    cases += [
        ("state-semantics", [], ["state"], set(CHECKS)),
        ("provider-semantics", [], ["provider"], {"reservations", "notifications", "smoke"}),
    ]
    for path in sorted({item for inputs in execution_inputs.values() for item in inputs}):
        required = {check for check, inputs in execution_inputs.items() if path in inputs} | {
            "smoke"
        }
        cases.append(("observed-implementation", [path], [], required))
    if changed_paths:
        required = {"smoke"}
        for path in changed_paths:
            owners = {check for check, inputs in execution_inputs.items() if path in inputs}
            if path.startswith("tests/test_") and path.endswith(".py"):
                owners |= {path.removeprefix("tests/test_").removesuffix(".py")} & set(CHECKS)
            required |= owners or set(CHECKS)
        cases.append(
            (
                "actual-patch",
                list(changed_paths),
                ["state"] if stage in (0, 2, 3) else ["provider"],
                required,
            )
        )
    plans = []
    for label, paths, contracts, required in cases:
        if "state" in contracts:
            required = set(CHECKS)
        elif "provider" in contracts:
            required |= {"reservations", "notifications", "smoke"}
        try:
            result = subprocess.run(
                [sys.executable, str(root / "ci_select.py")],
                input=json.dumps({"changed_paths": paths, "changed_contracts": contracts}),
                capture_output=True,
                text=True,
                cwd=root,
                timeout=10,
            )
            selected = json.loads(result.stdout)
            valid = (
                result.returncode == 0
                and isinstance(selected, list)
                and all(isinstance(value, str) and value in CHECKS for value in selected)
            )
        except (ValueError, subprocess.TimeoutExpired):
            selected, valid = [], False
        if not valid:
            selected = []
        plans.append(
            {
                "case": label,
                "changed_paths": paths,
                "changed_contracts": contracts,
                "required": sorted(required),
                "selected": selected,
                "safe": valid and required <= set(selected),
            }
        )
    behavior_passed, safe = not hidden["failures"], all(plan["safe"] for plan in plans)
    unchanged = before == fingerprint()
    return {
        "qualified": behavior_passed
        and safe
        and unchanged
        and all(check["passed"] for check in checks.values()),
        "behavior_passed": behavior_passed,
        "ci_safe": safe,
        "checkout_unchanged_during_checks": unchanged,
        "hidden": hidden,
        "candidate_checks": checks,
        "ci_cases": plans,
        "architecture_quality": "unscored: requires blinded human review of chosen design and later source patches",
        "boundary_violations": [],
        "independent_components": None,
        "observational_evidence": {
            "executed_inputs": execution_inputs,
            "shared_execution_inputs": sorted(
                set.intersection(
                    *(set(value) for key, value in execution_inputs.items() if key != "smoke")
                )
            ),
            "scope_basis": "Observed operation executions plus conservative shared/state/unknown widening; no gold source layout.",
        },
        "actual_evaluator_seconds": time.monotonic() - started,
        "actual_candidate_check_seconds": sum(
            check["elapsed_seconds"] for check in checks.values()
        ),
        "synthetic_ci_cost": {
            "basis": "equal check units; not measured CI runtime",
            "weights": dict.fromkeys(CHECKS, 1),
            "implementation_cases": [
                {
                    "changed_paths": plan["changed_paths"],
                    "selected_units": len(set(plan["selected"])),
                }
                for plan in plans
                if plan["case"] == "observed-implementation" and plan["safe"]
            ],
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--worker", type=Path)
    modes.add_argument("--capture-state", type=Path)
    parser.add_argument("stage", type=int)
    parser.add_argument("random_seed", type=int)
    args = parser.parse_args()
    if args.capture_state:
        root = args.capture_state.resolve()
        sys.dont_write_bytecode = True
        sys.pycache_prefix = str(
            root.parent / (".external-evaluator-cache-" + str(time.monotonic_ns()))
        )
        sys.path.insert(0, str(root))
        print(
            json.dumps(_compatibility_fixture(importlib.import_module("solution").execute, root, 1))
        )
    else:
        previous = json.loads(sys.stdin.read() or "null")
        print(json.dumps(behavior(args.worker.resolve(), args.stage, args.random_seed, previous)))
