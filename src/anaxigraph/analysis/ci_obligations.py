"""Compare supplied CI observations; never infer safety from directory names."""

from __future__ import annotations

from typing import Any

REPORT_PATH = "anaxigraph/ci-evidence.json"


def evidence_inputs(fingerprints: dict[str, str]) -> dict[str, str]:
    """An exact input inventory catches new, removed and changed consumers too."""
    return {path: value for path, value in fingerprints.items() if path != REPORT_PATH}


def _ids(value: Any) -> set[str]:
    if not isinstance(value, list) or len(value) > 20_000:
        raise ValueError("CI test IDs must be a bounded list")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError("CI test IDs must be nonempty strings")
    if len(set(value)) != len(value):
        raise ValueError("CI test IDs must be distinct")
    return set(value)


def _references(value: Any, tests: list[str]) -> set[str]:
    if isinstance(value, list) and value and type(value[0]) is int:
        if len(value) > 20_000 or any(
            type(index) is not int or not 0 <= index < len(tests) for index in value
        ):
            raise ValueError("CI test indices must identify the supplied inventory")
        if len(set(value)) != len(value):
            raise ValueError("CI test indices must be distinct")
        return {tests[index] for index in value}
    return _ids(value)


def _file(row: Any, tests: list[str], inputs: dict) -> dict:
    if not isinstance(row, dict) or row.get("path") not in inputs:
        raise ValueError("CI observations must identify fingerprinted source inputs")
    observed, selected = (
        _references(row.get("executing"), tests),
        _references(row.get("selected"), tests),
    )
    if not (observed | selected) <= set(tests):
        raise ValueError("CI observations contain test IDs outside the supplied inventory")
    omitted = observed - selected
    substitutions = row.get("substitutions", [])
    if not isinstance(substitutions, list) or len(substitutions) > 100:
        raise ValueError("CI substitutions must be a bounded list")
    for item in substitutions:
        if not isinstance(item, dict) or item.get("contract_path") not in inputs:
            raise ValueError("CI substitutions need a fingerprinted contract_path")
        if (
            not _references(item.get("omitted"), tests) <= omitted
            or not _references(item.get("replacements"), tests) <= selected
        ):
            raise ValueError("CI substitutions must reference omitted and selected tests")
    return {
        "path": row["path"],
        "executing": len(observed),
        "selected": len(selected),
        "executing_selected": len(observed & selected),
        "omitted_count": len(omitted),
        "omitted_tests": sorted(omitted)[:8],
        "omitted_tests_truncated": len(omitted) > 8,
        "substitution_status": "supplied-unverified" if substitutions else "not_supplied",
    }


def _faults(value: Any, inputs: dict) -> dict:
    if not isinstance(value, list) or len(value) > 1000:
        raise ValueError("CI faults must be a bounded list")
    detected, missed = 0, []
    for row in value:
        if not isinstance(row, dict) or row.get("path") not in inputs:
            raise ValueError("CI faults must name a fingerprinted input")
        if any(
            type(row.get(key)) is not bool for key in ("reference_detected", "selection_detected")
        ):
            raise ValueError("CI fault detection results must be explicit booleans")
        detected += row["reference_detected"]
        if row["reference_detected"] and not row["selection_detected"]:
            missed.append(row["path"])
    return {
        "sampled": len(value),
        "reference_detected": detected,
        "missed": len(missed),
        "missed_paths": sorted(set(missed))[:10],
        "status": "reported_missed" if missed else "sampled_passed" if detected else "unverified",
    }


def audit_selection(
    value: Any,
    fingerprints: dict[str, str],
    runtime_context: dict | None = None,
    changed_paths: list[str] | None = None,
) -> dict:
    """A report supplies observations, not permission to omit known consumers."""
    if value is None:
        return {"status": "not_supplied", "checks_executed": False, "path": REPORT_PATH}
    try:
        return _audit(value, fingerprints, runtime_context, changed_paths)
    except (TypeError, ValueError, KeyError) as error:
        return {"status": "invalid", "reason": str(error)[:300], "checks_executed": False}


def _environment(environment: Any) -> dict:
    if (
        not isinstance(environment, dict)
        or not environment
        or any(
            not isinstance(key, str)
            or not isinstance(item, str)
            or not key
            or not item
            or len(key) > 100
            or len(item) > 1000
            for key, item in environment.items()
        )
        or len(environment) > 32
    ):
        raise ValueError("CI evidence needs bounded explicit environment fingerprints")
    return environment


def _observations(value: dict) -> list[dict]:
    inventory = _ids(value.get("tests"))
    raw = value.get("files")
    if not inventory or not isinstance(raw, list) or not 1 <= len(raw) <= 5000:
        raise ValueError("CI evidence needs nonempty test and source observations")
    rows = [_file(row, value["tests"], value["inputs"]) for row in raw]
    by_path = {row["path"]: row for row in rows}
    if len(by_path) != len(rows):
        raise ValueError("CI source observations must have distinct paths")
    return rows


def _status(faults: dict, omitted: int, uncovered: list, matched: bool) -> str:
    if faults["missed"]:
        return "reported_fault_miss"
    if omitted:
        return "reported_gap"
    return "reported_passed" if matched and not uncovered else "unverified"


def _scoped_faults(value: dict, paths: list | None) -> dict:
    raw = value.get("faults", [])
    total = _faults(raw, value["inputs"])
    if paths is None:
        return total
    scoped = _faults([row for row in raw if row["path"] in paths], value["inputs"])
    return scoped | {"outside_scope_missed": total["missed"] - scoped["missed"]}


def _audit(value: dict, fingerprints: dict, runtime: dict | None, paths: list | None) -> dict:
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value["version"] != 1
    ):
        raise ValueError("CI evidence must be a version 1 object")
    if value.get("inputs") != evidence_inputs(fingerprints):
        return {
            "status": "stale",
            "reason": "Source/test/config inventory differs; refresh CI evidence.",
            "checks_executed": False,
        }
    environment = _environment(value.get("environment"))
    rows = _observations(value)
    return _summary(rows, value, paths, runtime, environment)


def _summary(
    rows: list[dict], value: dict, paths: list | None, runtime: dict | None, environment: dict
) -> dict:
    matched = runtime is not None and environment == runtime
    selected = [row for row in rows if paths is None or row["path"] in paths]
    uncovered = sorted(set(paths or []) - {row["path"] for row in rows})
    faults = _scoped_faults(value, paths)
    omitted = sum(row["omitted_count"] for row in selected)
    gaps = [row for row in selected if row["omitted_count"]]
    return {
        "status": _status(faults, omitted, uncovered, matched),
        "origin": "agent-or-CI-reported",
        "checks_executed": False,
        "environment_status": "matched"
        if matched
        else "not_compared"
        if runtime is None
        else "mismatched",
        "tests_total": len(value["tests"]),
        "files_total": len(rows),
        "files_in_scope": len(selected),
        "mean_selected_tests": sum(row["selected"] for row in selected) / len(selected)
        if selected
        else None,
        "omitted_execution_pairs": omitted,
        "findings": gaps[:10],
        "findings_truncated": len(gaps) > 10,
        "unobserved_paths": uncovered[:20],
        "unobserved_paths_total": len(uncovered),
        "faults": faults,
        "next_step": "Inspect omitted consumer obligations; validate proposed contract substitutions or widen affected checks.",
        "assurance": "Observed execution is a conservative signal, not proof every omitted test is necessary. Supplied substitutions and sampled faults do not establish complete behavioral equivalence.",
    }
