"""Small paid probe of implementation-sensitive advice, separate from coding outcomes."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from anaxigraph.analysis import canonical_result, validate_result
from anaxigraph.analysis.validation import ModelOutputError
from anaxigraph.application import Application
from anaxigraph.workflows.engine import DEFAULTS
from anaxigraph.workflows.providers import execute

HEADER = '''import asyncio

async def deliver(items, operation, concurrency=4):
    """Apply an asynchronous operation and preserve result order."""
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
'''
SOURCES = {
    "unbounded": HEADER + "    return await asyncio.gather(*(operation(item) for item in items))\n",
    "bounded": HEADER
    + """    results = []
    for start in range(0, len(items), concurrency):
        batch = items[start:start + concurrency]
        results.extend(await asyncio.gather(*(operation(item) for item in batch)))
    return results
""",
}
DEFAULT_BRIEF = {
    "overview": "Applies an asynchronous operation to a collection of items.",
    "detail": "Returns results in input order and rejects a nonpositive concurrency argument.",
    "capabilities": ["Apply an operation to items and return ordered results."],
    "function_overview": "Apply an operation to ordered items.",
}


async def peak_concurrency(source: str) -> int:
    """Execute only this experiment's fixed fixtures, never arbitrary repository source."""
    namespace: dict = {}
    exec(compile(source, "<controlled-fixture>", "exec"), namespace)
    active = peak = 0

    async def operation(item):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(0)
        active -= 1
        return item

    assert await namespace["deliver"](list(range(20)), operation, concurrency=4) == list(range(20))
    return peak


def fixture(root: Path, source: str, *, path="delivery.py", brief=None) -> Application:
    root.mkdir(parents=True)
    (root / path).write_text(source)
    brief = DEFAULT_BRIEF if brief is None else brief
    app = Application(root)
    app.scan()
    request = app.analysis_request("annotate", [path])
    app.submit_analysis(
        request["request_id"],
        {
            "producer": "synthetic-identical-behavior-brief",
            "annotations": [
                {
                    "node_id": request["scope"][0],
                    **{key: brief[key] for key in ("overview", "detail", "capabilities")},
                    "functions": [
                        {"node_id": node["id"], "overview": brief["function_overview"]}
                        for node in request["packet"]["functions"]
                    ],
                    "contracts": [],
                    "uncertainty": [],
                }
            ],
        },
    )
    return app


def run_case(app: Application, request: dict, label: str, output: Path) -> dict:
    attempts = []
    graph = app._current()
    profile = DEFAULTS["stages"][request["kind"]]
    for selected in [profile, *profile["fallbacks"]]:
        worker = {key: value for key, value in selected.items() if key != "fallbacks"}
        started = time.monotonic()
        attempt = {"worker": worker}
        try:
            result = execute(request, worker, timeout=None)
            attempt["usage"] = result.get("_anaxigraph_usage")
            result = canonical_result(request, result)
            attempt["candidate"] = result
            validate_result(request, result, graph)
            attempt["result"] = attempt.pop("candidate")
        except ModelOutputError as error:
            attempt["error"] = type(error).__name__
            attempt["validation_error"] = str(error)[:500]
        except Exception as error:
            attempt["error"] = type(error).__name__
        attempt["elapsed_seconds"] = round(time.monotonic() - started, 3)
        attempts.append(attempt)
        row = {
            "case": label,
            "request_id": request["request_id"],
            "input_bytes": len(json.dumps(request, ensure_ascii=False).encode()),
            "implementation_supplied": "implementation_evidence" in request,
            "attempts": attempts,
        }
        (output / f"{label}.json").write_text(json.dumps(row, indent=2) + "\n")
        if "result" in attempt:
            break
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory outside the repo")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    cases, peaks = [], {}
    for name, source in SOURCES.items():
        peaks[name] = asyncio.run(peak_concurrency(source))
        app = fixture(args.output / name, source)
        detail = app.analysis_request(
            "patterns", ["delivery.py"], pattern_ids=["unbounded-concurrency"]
        )
        facts = {key: value for key, value in detail.items() if key != "implementation_evidence"}
        screen = app.analysis_request("pattern-screen", ["delivery.py"])
        cases.extend(
            (app, request, f"{name}-{mode}", args.output)
            for mode, request in (("facts", facts), ("source", detail), ("screen", screen))
        )
    with ThreadPoolExecutor(max_workers=2) as workers:
        rows = list(workers.map(lambda case: run_case(*case), cases))
    report = {"peak_concurrency": peaks, "concurrency_argument": 4, "results": rows}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if any("result" not in row["attempts"][-1] for row in rows):
        raise SystemExit("Probe incomplete; inspect saved attempts")


if __name__ == "__main__":
    main()
