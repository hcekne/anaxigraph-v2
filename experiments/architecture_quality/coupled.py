"""A deliberately cross-cutting legacy seed under the existing discovery contract.

This is a controlled synthetic seed variant, not held-out maintenance evidence.
Only its starting source differs; tasks, behavioral oracle and calibration remain
the discovery track's. Worker-facing files do not prescribe a replacement design.
"""

from __future__ import annotations

import ast
from pathlib import Path

try:
    from . import discovery
except ImportError:
    import discovery

NAME = "workshop-coupled"
SCENARIOS = (NAME,)
CHECKS = discovery.CHECKS


def _check_name(name):
    if name != NAME:
        raise ValueError(name)


def brief(name=NAME):
    _check_name(name)
    return discovery.brief()


def task(name, stage):
    _check_name(name)
    return discovery.task(discovery.NAME, stage)


def _starting_source(root: Path):
    source = (root / "solution.py").read_text()
    functions = {
        node.name: ast.get_source_segment(source, node) + "\n"
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
    }
    reserve = functions["_reserve"].replace(
        "sum(item['quantity'] for item in draft['bookings'].values()",
        "sum(1 for item in draft['bookings'].values()",
    )
    reserve = reserve.replace("occupied + quantity", "occupied + 1")
    reserve = reserve.replace(
        "    amount = configuration['catalog'][workshop]['price_cents'] * quantity + configuration['fee_cents']\n",
        "",
    ).replace(
        "        answer = provider('charge', {'request_id': identity, 'workshop': workshop, 'customer': customer, 'amount_cents': amount})",
        "        amount, answer = _charge(provider, identity, workshop, customer, configuration)",
    )
    reserve = reserve.replace(
        "accepted, pending = _reply(answer, 'charge')", "accepted = bool(answer)"
    )
    reserve = reserve.replace(
        "'payment_pending' if pending else 'payment_declined'", "'payment_declined'"
    )
    reserve = reserve.replace("'quantity': quantity", "'quantity': 1")
    flush = functions["_flush"].replace("'quantity': booking['quantity']", "'quantity': 1")
    flush = flush.replace(
        "_day(configuration, booking['workshop'])",
        "_message_day(configuration, booking['workshop'])",
    )
    flush = flush.replace("accepted, _ = _reply(answer, 'notify')", "accepted = bool(answer)")
    summary = functions["_summary"].replace(
        "sum(item['quantity'] for item in bookings)", "len(bookings)"
    )
    imports = "import copy\nimport json\nfrom datetime import datetime, timedelta\nfrom pathlib import Path\n\n"
    records = (
        imports
        + "\n".join(
            functions[name]
            for name in ("_text", "_integer", "_configuration", "_state", "_commit", "_day")
        )
        + "\n"
        + summary
    )
    channels = (
        "import copy\nfrom datetime import datetime, timedelta\n"
        "from records import _integer, _state, _commit\n\n"
        + functions["_day"].replace("def _day(", "def _message_day(")
        + "\n"
        + _CHARGE
        + "\n"
        + flush
    )
    facade = (
        "import copy\nfrom records import _configuration, _text, _integer, _state, _commit, _summary\n"
        "from channels import _charge, _flush\n\n" + reserve + "\n" + functions["execute"]
    )
    for path, content in {
        "solution.py": facade,
        "records.py": records,
        "channels.py": channels,
    }.items():
        (root / path).write_text(content)


_CHARGE = """def _charge(provider, identity, workshop, customer, configuration):
    amount = configuration['catalog'][workshop]['price_cents'] + configuration['fee_cents']
    answer = provider('charge', {'request_id': identity, 'workshop': workshop, 'customer': customer, 'amount_cents': amount})
    return amount, answer
"""


def seed(root: Path, name=NAME):
    _check_name(name)
    discovery.seed(root)
    _starting_source(root)


def capture_state(root: Path):
    return discovery.capture_state(root)


def synthetic_step(root: Path, name: str, stage: int, treatment: bool):
    """Reuse known calibrations, not a claimed implementation of model advice."""
    _check_name(name)
    # Only the initial source is coupled. Subsequent synthetic layouts remain the
    # existing evaluator controls, carrying the actual baseline's opaque state.
    for name in ("records.py", "channels.py"):
        (root / name).unlink(missing_ok=True)
    discovery.synthetic_step(root, discovery.NAME, stage, treatment)


def evaluate(root: Path, name=NAME, stage=0, **options):
    _check_name(name)
    return discovery.evaluate(root, discovery.NAME, stage, **options)
