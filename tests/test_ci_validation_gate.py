"""Passing local checks never override missing consumer or suite obligations."""

import json

import pytest

from anaxigraph.analysis.ci_obligations import REPORT_PATH, evidence_inputs
from anaxigraph.application import Application

RUNTIME = {"python": "fixture-python", "dependencies": "locked-fixture"}
TESTS = ["tests/test_local.py::test_cache", "tests/test_consumer.py::test_tenant"]


@pytest.fixture
def candidate(tmp_path):
    (tmp_path / "cache.py").write_text(
        'raise RuntimeError("Analyzed project source must not execute")\nvalue = 1\n'
    )
    (tmp_path / "consumer.py").write_text("from cache import value\n")
    (tmp_path / "tests").mkdir()
    for name in ("test_local.py", "test_consumer.py"):
        (tmp_path / "tests" / name).write_text("def test_example():\n    assert True\n")
    (tmp_path / "ci.json").write_text(json.dumps({"selector": ["must-not-execute"]}))
    app = Application(tmp_path)
    app.scan()
    baseline = app.prepare_change("Change cache behavior while preserving tenant consumers")
    with (tmp_path / "cache.py").open("a") as stream:
        stream.write("changed = True\n")
    app.scan()
    return app, baseline["baseline_id"]


def selection_report(app):
    return {
        "version": 1,
        "inputs": evidence_inputs(dict(app._current().snapshot.files)),
        "environment": dict(RUNTIME),
        "tests": list(TESTS),
        "suite": {"tests": 2, "passed": 2, "returncode": 0},
        "files": [
            {"path": path, "executing": [0, 1], "selected": [0, 1]}
            for path in ("cache.py", "consumer.py")
        ],
        "change_selection": {"changed_paths": ["cache.py"], "selected_tests": [0, 1]},
        "faults": [],
    }


def write_report(app, value):
    path = app.root / REPORT_PATH
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(value))
    app.scan()


def validate(candidate, *, runtime=RUNTIME, exit_code=0, require=False):
    app, baseline = candidate
    return app.validate_change(
        [
            {
                "command": "pytest tests/test_local.py",
                "snapshot_id": app._current().snapshot.id,
                "exit_code": exit_code,
            }
        ],
        baseline,
        runtime_context=runtime,
        require_ci_evidence=require,
    )


def test_ordinary_change_without_ci_configuration_preserves_reported_check_success(candidate):
    app, _ = candidate
    (app.root / "ci.json").unlink()
    app.scan()
    baseline = app.prepare_change("Ordinary local change")
    with (app.root / "cache.py").open("a") as stream:
        stream.write("another_change = True\n")
    app.scan()
    result = validate((app, baseline["baseline_id"]))
    assert result["validation_status"] == "reported-passed"
    assert result["completion_allowed"] is True
    assert result["ci_selection"]["required"] is False


def test_gap_and_passing_local_receipt_cannot_authorize_completion(candidate):
    app, _ = candidate
    value = selection_report(app)
    value["files"][0]["selected"] = [0]
    value["change_selection"]["selected_tests"] = [0]
    value["faults"] = [{"path": "cache.py", "reference_detected": True, "selection_detected": True}]
    write_report(app, value)
    result = validate(candidate)
    assert result["checks"][0]["status"] == "passed"
    assert result["ci_selection"]["status"] == "reported_gap"
    assert result["validation_status"] == "blocked"
    assert result["completion_allowed"] is False


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "stale",
        "environment",
        "unbound",
        "missing_source_row",
        "missing_suite",
        "zero_tests",
    ],
)
def test_required_ci_with_missing_or_incomplete_evidence_is_not_green(candidate, problem):
    app, _ = candidate
    if problem != "missing":
        value = selection_report(app)
        if problem == "missing_source_row":
            value["files"] = value["files"][:1]
        elif problem == "missing_suite":
            value.pop("suite")
        elif problem == "zero_tests":
            value["suite"] = {"tests": 0, "passed": 0, "returncode": 0}
        write_report(app, value)
    if problem == "stale":
        (app.root / "consumer.py").write_text("from cache import changed\n")
        app.scan()
    runtime = (
        {"python": "other-runtime"}
        if problem == "environment"
        else None
        if problem == "unbound"
        else RUNTIME
    )
    result = validate(candidate, runtime=runtime)
    assert result["validation_status"] == "incomplete"
    assert result["completion_allowed"] is False
    assert result["ci_selection"]["required"] is True


@pytest.mark.parametrize(
    "problem", ["missed_fault", "substitution", "failed_suite", "failed_receipt"]
)
def test_explicit_safety_failures_block_even_when_local_checks_pass(candidate, problem):
    app, _ = candidate
    value = selection_report(app)
    if problem == "missed_fault":
        value["faults"] = [
            {"path": "cache.py", "reference_detected": True, "selection_detected": False}
        ]
    elif problem == "substitution":
        value["files"][0].update(
            selected=[0],
            substitutions=[
                {
                    "contract_path": "consumer.py",
                    "omitted": [1],
                    "replacements": [0],
                }
            ],
        )
    elif problem == "failed_suite":
        value["suite"].update(passed=1, returncode=1)
    write_report(app, value)
    result = validate(candidate, exit_code=1 if problem == "failed_receipt" else 0)
    assert result["validation_status"] == "blocked"
    assert result["completion_allowed"] is False


def test_fresh_complete_supplied_ci_and_checks_allow_only_reported_success(candidate):
    app, _ = candidate
    write_report(app, selection_report(app))
    result = validate(candidate)
    assert result["validation_status"] == "reported-passed"
    assert result["completion_allowed"] is True
    assert result["ci_selection"]["ready"] is True
    assert result["ci_selection"]["checks_executed"] is False
    assert result["checks"][0]["origin"] == "agent-or-CI-reported"


def test_explicit_required_ci_and_absent_receipts_remain_unchecked(candidate):
    app, baseline = candidate
    write_report(app, selection_report(app))
    result = app.validate_change(
        baseline_id=baseline, runtime_context=RUNTIME, require_ci_evidence=True
    )
    assert result["validation_status"] == "unchecked"
    assert result["completion_allowed"] is False


def test_explicit_flag_requires_ci_evidence_without_known_configuration(candidate):
    app, _ = candidate
    (app.root / "ci.json").unlink()
    app.scan()
    baseline = app.prepare_change("Explicitly qualify this change")
    with (app.root / "cache.py").open("a") as stream:
        stream.write("explicit_change = True\n")
    app.scan()
    result = validate((app, baseline["baseline_id"]), require=True)
    assert result["ci_selection"]["required"] is True
    assert result["validation_status"] == "incomplete"
    assert result["completion_allowed"] is False


def test_individual_file_selection_cannot_hide_a_nonmonotonic_combined_selection(candidate):
    app, _ = candidate
    (app.root / "consumer.py").write_text("from cache import changed\n")
    app.scan()
    value = selection_report(app)
    value["change_selection"] = {
        "changed_paths": ["cache.py", "consumer.py"],
        "selected_tests": [0],
        "status": "reported_passed",
        "required_tests": [0],
        "omitted_tests": [],
    }
    write_report(app, value)
    result = validate(candidate)
    assert result["validation_status"] == "blocked"
    assert result["completion_allowed"] is False
    assert all(row["selected"] == [0, 1] for row in value["files"])


def test_batch_evidence_for_another_diff_is_incomplete(candidate):
    app, _ = candidate
    value = selection_report(app)
    value["change_selection"]["changed_paths"] = ["consumer.py"]
    write_report(app, value)
    result = validate(candidate)
    assert result["validation_status"] == "incomplete"
    assert result["completion_allowed"] is False


def test_unknown_changed_configuration_requires_the_full_inventory(candidate):
    app, _ = candidate
    (app.root / "settings.toml").write_text('feature = "changed"\n')
    app.scan()
    value = selection_report(app)
    value["tests"].append("tests/test_external.py::test_configuration")
    value["suite"] = {"tests": 3, "passed": 3, "returncode": 0}
    value["change_selection"]["changed_paths"] = ["cache.py", "settings.toml"]
    write_report(app, value)
    result = validate(candidate)
    assert result["validation_status"] == "blocked"
    assert result["completion_allowed"] is False
    value["change_selection"]["selected_tests"] = [0, 1, 2]
    write_report(app, value)
    complete = validate(candidate)
    assert complete["validation_status"] == "reported-passed"
    assert complete["completion_allowed"] is True


def test_docs_only_change_does_not_inherit_an_unrelated_consumer_gap(candidate):
    app, _ = candidate
    baseline = app.prepare_change("Clarify documentation")
    (app.root / "docs").mkdir()
    (app.root / "docs" / "usage.md").write_text("Clarified instructions.\n")
    app.scan()
    value = selection_report(app)
    value["files"][0]["selected"] = [0]
    write_report(app, value)
    result = validate((app, baseline["baseline_id"]))
    assert result["ci_selection"]["required"] is False
    assert result["validation_status"] == "reported-passed"
    assert result["completion_allowed"] is True
