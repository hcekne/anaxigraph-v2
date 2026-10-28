"""Small applications with public behavior and deliberately removable coupling.

The modular fixtures qualify the evaluator. Every live pair starts from the SAME
coupled fixture; fixture variants are never experimental treatment assignments.
"""

from __future__ import annotations

import json
from pathlib import Path

SCENARIOS = {
    "checkout": {
        "purpose": "Quote and submit a cart to an injected payment authorization provider.",
        "fields": ("quantity", "unit_cents"),
        "formula": "quantity * unit_cents",
        "extension": "discount_cents",
        "later": "Accept discount_cents (default 0, nonnegative integer), subtract at most the "
        "entire pre-multiplier cart price, then apply multiplier. Never authorize a negative quote.",
    },
    "dispatch": {
        "purpose": "Price and submit a notification batch to an injected message provider.",
        "fields": ("recipient_count", "payload_bytes"),
        "formula": "recipient_count * ((payload_bytes + 1023) // 1024)",
        "extension": "priority",
        "later": "Accept priority (default 1, integer from 1 through 3) and multiply the "
        "pre-multiplier notification price by priority. Reject other priorities with ValueError.",
    },
    "reporting": {
        "purpose": "Price and submit a tabular export to an injected report storage provider.",
        "fields": ("rows", "columns"),
        "formula": "rows * columns",
        "extension": "free_rows",
        "later": "Accept free_rows (default 0, nonnegative integer), exclude at most all rows "
        "before multiplying by columns and multiplier. Never submit a negative export price.",
    },
}

CHECKS = ["compute", "delivery", "integration"]


def oracle(name: str, request: dict, settings: dict, stage: int) -> int:
    """Independent behavioral reference; not imported into generated applications."""
    spec = SCENARIOS[name]
    left, right = (request.get(field) for field in spec["fields"])
    reference, multiplier = request.get("reference"), settings.get("multiplier")
    if not isinstance(reference, str) or not reference:
        raise ValueError("reference")
    if any(type(value) is not int or value < 0 for value in (left, right)):
        raise ValueError("nonnegative integer fields")
    if type(multiplier) is not int or multiplier < 1:
        raise ValueError("multiplier")
    if name == "dispatch":
        value = left * ((right + 1023) // 1024)
    else:
        value = left * right
    if stage >= 1:
        extra = request.get(spec["extension"], 1 if name == "dispatch" else 0)
        if type(extra) is not int or extra < (1 if name == "dispatch" else 0):
            raise ValueError("extension")
        if name == "dispatch":
            if extra > 3:
                raise ValueError("priority")
            value *= extra
        elif name == "checkout":
            value = max(0, value - extra)
        else:
            value = max(0, left - extra) * right
    return value * multiplier


def brief(name: str) -> str:
    spec = SCENARIOS[name]
    left, right = spec["fields"]
    return f"""{spec["purpose"]}
Public operation solution.execute(request, settings=None, dependency=None) returns a dict.
request includes a nonempty string reference and nonnegative integer {left}, {right}.
Booleans are not integers for this contract. Missing/invalid fields raise ValueError.
settings contains positive integer multiplier; default settings come from settings.json.
The baseline value is ({spec["formula"]}) * multiplier, with arbitrary precision integers.
Call dependency(reference, value) exactly once after validation. Return reference, value,
and accepted (the boolean result). The default dependency accepts. OSError becomes
accepted=False,error='unavailable'. Other exceptions propagate. Do not mutate inputs.
settings and dependency are explicit replaceable dependencies. No external services are needed.

Publish compute(request, settings)->integer and delivery(reference,value,dependency)->dict
entrypoints in components.json, with components.compute / components.delivery strings in
module:function form, and inputs.compute / inputs.delivery lists of owned source paths.
The entrypoint names and layout are your choice. Components may be ordinary functions in
one process. Neither component may require the other component to import or execute its
own behavior. Private cross-component accesses and dependency cycles violate the contract.
Document input validation, failure and side-effect guarantees in contracts.md and test them.

ci_select.py reads JSON from stdin with changed_paths and changed_contracts lists and emits
a JSON list of check IDs: compute, delivery, integration. Each check has a corresponding
independently executable tests/test_<id>.py file (stdlib unittest). Internal implementation
changes should select their owner plus integration. A changed public behavior contract
selects its owner, its consumer delivery, and integration (delivery has no compute consumer).
settings.json, schema.json, pyproject.toml, ci_select.py, components.json and unknown paths
affect all checks, including when signatures stay unchanged. Retain integration always.
Source ownership must agree with reality, including transitive local imports and mutable
shared state. A shared source file legitimately widens scope. Do not delete checks to narrow CI.
"""


def task(name: str, stage: int) -> str:
    if stage == 0:
        return (
            "Milestone: preserve all existing public behavior while improving responsibility "
            "ownership, public behavioral contracts, independent verification and honest CI scope. "
            "Use the simplest useful design. Complete the published component and CI contracts."
        )
    if stage == 1:
        return "Later policy change: " + SCENARIOS[name]["later"]
    if stage == 2:
        return (
            "Later adapter change: dependency may return {'ok': bool, 'receipt': str}. Preserve "
            "old boolean providers. For dict responses use ok for accepted and return receipt; "
            "do not change compute behavior or its public contract. Keep OSError behavior."
        )
    return (
        "Shared configuration change: settings.json multiplier becomes 3. Add schema.json "
        "with {'version': 2, 'multiplier_semantics': 'all_values'}. Both calculation and delivery "
        "must observe the new values with unchanged public signatures. Verify all dependent "
        "checks. Explain why a signature-only CI planner would be unsafe."
    )


def seed(root: Path, name: str, *, modular: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    spec = SCENARIOS[name]
    left, right = spec["fields"]
    calculation = f"""def compute(request, settings):
    reference = request.get('reference')
    left, right = request.get('{left}'), request.get('{right}')
    multiplier = settings.get('multiplier')
    if not isinstance(reference, str) or not reference:
        raise ValueError('reference')
    if any(type(value) is not int or value < 0 for value in (left, right)):
        raise ValueError('nonnegative integer fields')
    if type(multiplier) is not int or multiplier < 1:
        raise ValueError('multiplier')
    value = {spec["formula"].replace(left, "left").replace(right, "right")}
    return value * multiplier
"""
    if not modular:
        calculation = "import adapter\n\n" + calculation.replace(
            "return value * multiplier", "return adapter._internal_scale(value, multiplier)"
        )
    files = {
        "compute.py": calculation,
        "adapter.py": """def _internal_scale(value, multiplier):
    return value * multiplier


def deliver(reference, value, dependency):
    try:
        return {'reference': reference, 'value': value,
                'accepted': bool(dependency(reference, value))}
    except OSError:
        return {'reference': reference, 'value': value,
                'accepted': False, 'error': 'unavailable'}
""",
        "solution.py": """import json
from pathlib import Path
from compute import compute
from adapter import deliver


def execute(request, settings=None, dependency=None):
    if settings is None:
        settings = json.loads(Path(__file__).with_name('settings.json').read_text())
    if dependency is None:
        dependency = lambda reference, value: True
    value = compute(request, settings)
    return deliver(request['reference'], value, dependency)
""",
        "settings.json": '{"multiplier": 2}\n',
        "schema.json": '{"version": 1}\n',
        "pyproject.toml": '[project]\nname = "architecture-fixture"\nversion = "0.0.0"\n',
        "contracts.md": brief(name),
        "BRIEF.md": brief(name),
        "components.json": json.dumps(
            {
                "components": {"compute": "compute:compute", "delivery": "adapter:deliver"},
                "inputs": {"compute": ["compute.py"], "delivery": ["adapter.py"]},
            },
            indent=2,
        ),
        "ci_select.py": """import json
import sys
from pathlib import Path

change = json.load(sys.stdin)
manifest = json.loads(Path(__file__).with_name('components.json').read_text())
selected = {'integration'}
for path in change['changed_paths']:
    owners = {name for name, paths in manifest['inputs'].items() if path in paths}
    if not owners:
        selected.update(('compute', 'delivery'))
    else:
        selected.update(owners)
for contract in change.get('changed_contracts', []):
    selected.add(contract)
    if contract == 'compute':
        selected.add('delivery')
print(json.dumps(sorted(selected)))
""",
    }
    if not modular:
        # Honest coupled baseline: the delivery implementation is a calculation input too.
        manifest = json.loads(files["components.json"])
        manifest["inputs"]["compute"].append("adapter.py")
        files["components.json"] = json.dumps(manifest, indent=2)
    for check in CHECKS:
        import_line = {
            "compute": "from compute import compute",
            "delivery": "from adapter import deliver",
            "integration": "from solution import execute",
        }[check]
        assertion = {
            "compute": "        self.assertIsInstance(compute(request, {'multiplier': 2}), int)\n",
            "delivery": "        self.assertTrue(deliver('r', 8, lambda *args: True)['accepted'])\n",
            "integration": "        self.assertTrue(execute(request, {'multiplier': 2}, lambda *args: True)['accepted'])\n",
        }[check]
        files[f"tests/test_{check}.py"] = (
            "import unittest\n" + import_line + "\n\n"
            "class PublicBehavior(unittest.TestCase):\n"
            "    def test_example(self):\n"
            f"        request = {{'reference': 'r', '{left}': 2, '{right}': 4}}\n"
            + assertion
            + "\n"
            "if __name__ == '__main__':\n    unittest.main()\n"
        )
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
