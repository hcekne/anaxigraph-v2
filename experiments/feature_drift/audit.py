"""Read saved feature-drift evidence without executing candidates or calling models.

The output must be a new file outside the frozen state directory. Historical coverage
counts identify omissions, not the identities of omitted tests or proven escaped bugs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from statistics import mean


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path):
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def evidence_files(state: Path) -> list[Path]:
    paths = set()
    for pattern in (
        "*-r*/chain.json",
        "*-r*/steps.jsonl",
        "*-r*/eval/*/ci.json",
        "*-r*/logs/*.jsonl",
    ):
        paths.update(state.glob(pattern))
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(state):
            raise ValueError("Saved evidence must remain inside the state directory")
    return sorted(paths)


def fingerprint(state: Path) -> dict:
    files = {
        str(path.relative_to(state)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in evidence_files(state)
    }
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {
        "sha256": digest,
        "files": files,
        "scope": "saved checkpoint reports, step records and agent logs",
    }


def numeric(value):
    return type(value) in {int, float} and value >= 0


def average(values):
    values = [value for value in values if numeric(value)]
    return mean(values) if values else None


def checkpoint(ci: dict, row: dict, label: str) -> dict:
    per_file = ci.get("per_file")
    measured = [
        value
        for value in per_file or []
        if all(numeric(value.get(key)) for key in ("selected", "executing", "executing_selected"))
        and value["executing_selected"] <= value["executing"]
    ]
    omissions = [
        {
            "file": value["file"],
            "omitted_execution_pairs": value["executing"] - value["executing_selected"],
        }
        for value in measured
        if value["executing"] > value["executing_selected"]
    ]
    executing = sum(value["executing"] for value in measured) if measured else None
    omitted = sum(value["omitted_execution_pairs"] for value in omissions) if measured else None
    selected = average(value["selected"] for value in measured)
    tests = ci.get("tests_total")
    production = row.get("static", {}).get("prod_files")
    selector_failures = ci.get("selector_failures")
    complete = (
        bool(measured)
        and ci.get("coverage_run", {}).get("returncode") == 0
        and selector_failures == 0
    )
    if numeric(production):
        complete = complete and len(measured) == production
    else:
        complete = False
    mutants = ci.get("mutants") or {}
    counts = {
        key: mutants.get(key) for key in ("planted", "caught_by_suite", "missed_by_selection")
    }
    valid_mutants = all(numeric(value) for value in counts.values())
    sampled_status = (
        "selection_missed_reference_detected_sample"
        if valid_mutants and counts["missed_by_selection"]
        else "caught_all_reference_detected_samples"
        if valid_mutants and counts["caught_by_suite"]
        else "unknown"
    )
    return {
        "checkpoint": label,
        "step": row.get("step"),
        "production_files": production,
        "production_files_measured": len(measured) if per_file is not None else None,
        "production_files_unmeasured": max(0, production - len(measured))
        if numeric(production)
        else None,
        "test_denominator": tests,
        "mean_selected_tests": selected,
        "mean_selected_percent": 100 * selected / tests
        if selected is not None and numeric(tests) and tests
        else None,
        "mean_executing_tests": average(value["executing"] for value in measured),
        "observed_execution_pairs": executing,
        "omitted_execution_pairs": omitted,
        "files_with_omitted_consumers": len(omissions) if measured else None,
        "omissions": omissions,
        "executing_recall": (executing - omitted) / executing if executing else None,
        "coverage_completeness": "observed_gap"
        if omitted
        else "observed_complete"
        if complete
        else "unknown",
        "exact_omitted_test_ids": None,
        "omitted_identity_evidence": "not_recorded: saved per_file rows contain counts only",
        "contract_substitution_evidence": "unknown: counts and sampled mutants do not demonstrate substitution",
        "sampled_mutants": counts | {"status": sampled_status},
        "historical_escaped_bug": "not_established",
        "selector_failures": selector_failures,
        "coverage_run_seconds": ci.get("coverage_run", {}).get("seconds"),
        "selector_max_seconds": ci.get("selector_max_seconds"),
        "selected_suite_seconds": ci.get("selected_suite_seconds"),
        "selected_suite_timing_evidence": "not_recorded"
        if ci.get("selected_suite_seconds") is None
        else "saved_report",
    }


def result_body(result):
    if not isinstance(result, dict):
        return None
    for key in ("structured_content", "structuredContent"):
        if isinstance(result.get(key), dict):
            return result[key]
    text = "".join(
        block.get("text", "") for block in result.get("content", []) if block.get("type") == "text"
    )
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def tool_traces(chain: Path) -> dict:
    calls, errors, statuses, digests = Counter(), Counter(), Counter(), Counter()
    request_bytes = response_bytes = missing_results = no_material = unknown_assessments = 0
    log_files = sorted((chain / "logs").glob("*.jsonl"))
    for path in log_files:
        latest = {}
        for event in read_jsonl(path):
            item = event.get("item") or {}
            if item.get("type") == "mcp_tool_call":
                latest[item.get("id", len(latest))] = item
        for item in latest.values():
            name = str(item.get("server", "unknown")) + "." + str(item.get("tool", "unknown"))
            calls[name] += 1
            statuses[str(item.get("status", "unknown"))] += 1
            result = item.get("result")
            error = (
                bool(item.get("error"))
                or item.get("status") == "failed"
                or (isinstance(result, dict) and bool(result.get("isError")))
            )
            errors[name] += int(error)
            request_bytes += len(canonical_bytes(item.get("arguments")))
            body = result_body(result)
            if result is None:
                missing_results += 1
            else:
                response_bytes += len(canonical_bytes(result))
                digests[
                    (
                        name,
                        hashlib.sha256(
                            canonical_bytes(body if body is not None else result)
                        ).hexdigest(),
                    )
                ] += 1
            if item.get("tool") == "assess_plan":
                status = (body or {}).get("architecture", {}).get("status")
                no_material += status == "no_current_material_proposals"
                unknown_assessments += status is None
    return {
        "log_files": len(log_files),
        "tool_calls": sum(calls.values()),
        "tools": dict(calls),
        "tool_errors": sum(errors.values()),
        "errors_by_tool": {key: value for key, value in errors.items() if value},
        "terminal_statuses": dict(statuses),
        "request_payload_bytes": request_bytes,
        "response_payload_bytes": response_bytes,
        "payload_measurement": "canonical UTF-8 JSON of recorded arguments/results; not wire bytes or tokens",
        "calls_without_saved_results": missing_results,
        "no_material_assessments": no_material,
        "assessments_without_architecture_status": unknown_assessments,
        "repeated_result_digest_calls": sum(count - 1 for count in digests.values()),
        "repeated_result_digest_groups": sum(count > 1 for count in digests.values()),
        "result_digest_scope": "same tool and canonical decoded result within this chain",
        "tool_seconds": None,
        "tool_seconds_reason": "event stream lacks reliable per-call timing",
        "cost_usd": None,
        "cost_reason": "saved traces do not contain authoritative billed tool/model cost",
    }


def chain_audit(chain: Path) -> dict:
    rows = read_jsonl(chain / "steps.jsonl")
    by_step = {row["step"]: row for row in rows}
    metadata = read_json(chain / "chain.json") if (chain / "chain.json").exists() else {}
    checkpoints = []
    for path in sorted((chain / "eval").glob("*/ci.json")):
        label = path.parent.name
        step = int(label.removeprefix("step-")) if label.removeprefix("step-").isdigit() else None
        checkpoints.append(checkpoint(read_json(path), by_step.get(step, {}), label))
    return {
        "chain": chain.name,
        "arm": metadata.get("arm", chain.name.rsplit("-r", 1)[0]),
        "steps": len(rows),
        "checkpoints": checkpoints,
        "tools": tool_traces(chain),
        "agent_seconds": sum(row.get("agent", {}).get("seconds", 0) for row in rows),
        "analysis_seconds": sum(row.get("anaxigraph", {}).get("seconds", 0) for row in rows),
    }


def audit(state: Path) -> dict:
    state = state.resolve(strict=True)
    before = fingerprint(state)
    chains = [chain_audit(path) for path in sorted(state.glob("*-r*")) if path.is_dir()]
    arms = {}
    for arm in sorted({chain["arm"] for chain in chains}):
        selected = [chain for chain in chains if chain["arm"] == arm]
        checkpoints = [item for chain in selected for item in chain["checkpoints"]]
        arms[arm] = {
            "chains": selected,
            "checkpoint_count": len(checkpoints),
            "mean_selected_tests": average(item["mean_selected_tests"] for item in checkpoints),
            "mean_selected_percent": average(item["mean_selected_percent"] for item in checkpoints),
            "omitted_execution_pairs": sum(
                item["omitted_execution_pairs"] or 0 for item in checkpoints
            ),
            "checkpoints_with_unknown_omissions": sum(
                item["omitted_execution_pairs"] is None for item in checkpoints
            ),
            "checkpoints_with_observed_gaps": sum(
                item["coverage_completeness"] == "observed_gap" for item in checkpoints
            ),
            "tool_calls": sum(chain["tools"]["tool_calls"] for chain in selected),
            "tool_errors": sum(chain["tools"]["tool_errors"] for chain in selected),
            "response_payload_bytes": sum(
                chain["tools"]["response_payload_bytes"] for chain in selected
            ),
            "no_material_assessments": sum(
                chain["tools"]["no_material_assessments"] for chain in selected
            ),
            "repeated_result_digest_calls": sum(
                chain["tools"]["repeated_result_digest_calls"] for chain in selected
            ),
            "cost_usd": None,
        }
    after = fingerprint(state)
    if before != after:
        raise ValueError("Saved evidence changed while auditing; output was not written")
    return {
        "version": 1,
        "state": str(state),
        "mode": "saved_evidence_only",
        "candidate_execution": "not_performed",
        "model_calls": 0,
        "input_fingerprint": before,
        "input_unchanged": True,
        "arms": arms,
    }


def write_new_output(state: Path, output: Path) -> None:
    if output.exists() or output.is_symlink():
        raise ValueError("Output already exists; choose a new path")
    state, output = state.resolve(strict=True), output.resolve()
    if output.is_relative_to(state):
        raise ValueError("Output must be outside the frozen state directory")
    if output.exists():
        raise ValueError("Output already exists; choose a new path")
    report = audit(state)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".retrospective-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)  # Atomic exclusive publication; never overwrite another record.
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        write_new_output(args.state, args.output)
    except (ValueError, OSError) as error:
        parser.exit(2, f"audit: {error}\n")
    print(f"Saved read-only retrospective audit to {args.output}")


if __name__ == "__main__":
    main()
