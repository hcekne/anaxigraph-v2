"""Additional controlled opportunities and already-good counterexamples; paid when run."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .implementation import fixture, run_case

POLICY = """    if amount < 0:
        raise ValueError("amount must be nonnegative")
    if promotion == "launch":
        discount = 0.10
    elif customer == "member":
        discount = 0.05
    else:
        discount = 0
    return round(amount * (1 - discount), 2)
"""
PRICING_BRIEF = {
    "overview": "Calculates preview and final totals under one discount policy.",
    "detail": "Both totals apply the same launch promotion and membership discount rules and reject negative amounts.",
    "capabilities": [
        "Calculate consistent preview and final totals under the same discount policy."
    ],
    "function_overview": "Calculate a discounted total while rejecting negative amounts.",
}
RECEIPT_BRIEF = {
    "overview": "Formats receipt text for a caller.",
    "detail": "Returns a two-decimal total. Choosing whether and how to persist the receipt belongs to the caller.",
    "capabilities": ["Produce formatted receipt text while leaving persistence to the caller."],
    "function_overview": "Return formatted receipt text.",
}
CASES = {
    "duplicated-policy": {
        "path": "pricing.py",
        "pattern": "duplicated-branch-logic",
        "brief": PRICING_BRIEF,
        "source": "def preview(amount, customer, promotion):\n"
        + POLICY
        + "\ndef final(amount, customer, promotion):\n"
        + POLICY,
    },
    "shared-policy": {
        "path": "pricing.py",
        "pattern": "duplicated-branch-logic",
        "brief": PRICING_BRIEF,
        "source": "def _total(amount, customer, promotion):\n"
        + POLICY
        + "\ndef preview(amount, customer, promotion):\n    return _total(amount, customer, promotion)\n"
        + "\ndef final(amount, customer, promotion):\n    return _total(amount, customer, promotion)\n",
    },
    "hidden-write": {
        "path": "receipt.py",
        "pattern": "hidden-side-effect",
        "brief": RECEIPT_BRIEF,
        "source": '''def format_receipt(amount):
    """Return formatted text; callers own persistence."""
    text = f"Total: {amount:.2f}"
    with open("last-receipt.txt", "w") as stream:
        stream.write(text)
    return text
''',
    },
    "pure-formatting": {
        "path": "receipt.py",
        "pattern": "hidden-side-effect",
        "brief": RECEIPT_BRIEF,
        "source": '''def format_receipt(amount):
    """Return formatted text; callers own persistence."""
    return f"Total: {amount:.2f}"
''',
    },
}


def pricing_oracle(source: str) -> dict:
    """Execute this probe's fixed fixtures, including a single-site policy update."""
    outcomes = []
    for code in (source, source.replace("0.10", "0.20", 1)):
        namespace: dict = {}
        exec(compile(code, "<controlled-pricing>", "exec"), namespace)
        outcomes.append([namespace[name](100, "member", "launch") for name in ("preview", "final")])
    return {"original_totals": outcomes[0], "single_edit_totals": outcomes[1]}


def receipt_oracle(source: str, directory: Path) -> dict:
    directory.mkdir()
    result = subprocess.run(
        [sys.executable, "-c", source + "\nprint(format_receipt(12.5))"],
        cwd=directory,
        capture_output=True,
        text=True,
        check=True,
    )
    return {
        "text": result.stdout.strip(),
        "files_written": sorted(p.name for p in directory.iterdir()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    jobs, observed = [], {}
    for name, case in CASES.items():
        observed[name] = (
            pricing_oracle(case["source"])
            if case["path"] == "pricing.py"
            else receipt_oracle(case["source"], args.output / f"{name}-oracle")
        )
        app = fixture(args.output / name, case["source"], path=case["path"], brief=case["brief"])
        for mode, options in (("source", {"pattern_ids": [case["pattern"]]}), ("screen", {})):
            request = app.analysis_request(
                "patterns" if mode == "source" else "pattern-screen", [case["path"]], **options
            )
            jobs.append((app, request, f"{name}-{mode}", args.output))
    with ThreadPoolExecutor(max_workers=2) as workers:
        rows = []
        for kind in ("pattern-screen", "patterns"):
            stage = [job for job in jobs if job[1]["kind"] == kind]
            rows.extend(workers.map(lambda job: run_case(*job), stage))
    report = {"observed": observed, "results": rows}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if any("result" not in row["attempts"][-1] for row in rows):
        raise SystemExit("Probe incomplete; inspect saved attempts")


if __name__ == "__main__":
    main()
