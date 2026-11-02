"""Supplied observations expose unsafe CI assumptions without running target code."""

import copy
import hashlib
import json

from anaxigraph.analysis.ci_obligations import REPORT_PATH, audit_selection
from anaxigraph.application import Application


def report(files):
    return {
        "version": 1,
        "inputs": files,
        "environment": {"python": "test-runtime"},
        "tests": ["tests/test_local.py::test_cache", "tests/test_consumer.py::test_tenant"],
        "files": [
            {
                "path": "cache.py",
                "executing": ["tests/test_consumer.py::test_tenant"],
                "selected": ["tests/test_local.py::test_cache"],
            }
        ],
        "faults": [],
    }


def test_exact_omitted_consumer_and_unverified_substitution_survive_fault_sample_success():
    inputs = {"cache.py": "abc", "contracts.py": "def"}
    value = report(inputs)
    value["faults"] = [{"path": "cache.py", "reference_detected": True, "selection_detected": True}]
    value["files"][0]["substitutions"] = [
        {
            "contract_path": "contracts.py",
            "omitted": [value["tests"][1]],
            "replacements": [value["tests"][0]],
        }
    ]
    result = audit_selection(value, inputs, value["environment"])
    assert result["status"] == "reported_gap"
    assert result["faults"]["status"] == "sampled_passed"
    assert result["findings"][0]["omitted_tests"] == [value["tests"][1]]
    assert result["findings"][0]["substitution_status"] == "supplied-unverified"


def test_reported_fault_miss_is_a_failure_even_when_observed_coverage_is_complete():
    inputs = {"cache.py": "abc"}
    value = report(inputs)
    value["files"][0]["selected"] = value["tests"]
    value["faults"] = [
        {"path": "cache.py", "reference_detected": True, "selection_detected": False}
    ]
    assert audit_selection(value, inputs)["status"] == "reported_fault_miss"


def test_unrelated_fault_is_disclosed_without_expanding_an_independent_change():
    inputs = {"cache.py": "abc", "other.py": "def"}
    value = report(inputs)
    value["files"].append({"path": "other.py", "executing": [0], "selected": [0]})
    value["faults"] = [
        {"path": "cache.py", "reference_detected": True, "selection_detected": False}
    ]
    scoped = audit_selection(value, inputs, value["environment"], ["other.py"])
    assert scoped["status"] == "reported_passed"
    assert scoped["faults"]["outside_scope_missed"] == 1
    assert audit_selection(value, inputs)["status"] == "reported_fault_miss"


def test_interned_inventory_preserves_omitted_identities_and_rejects_invalid_indices():
    inputs = {"cache.py": "abc"}
    value = report(inputs)
    value["files"][0].update(executing=[1], selected=[0])
    result = audit_selection(value, inputs)
    assert result["findings"][0]["omitted_tests"] == [value["tests"][1]]
    for invalid in ([2], [0, True], [0, 0], [-1]):
        value["files"][0]["selected"] = invalid
        assert audit_selection(value, inputs)["status"] == "invalid"


def test_valid_long_parametrized_test_id_uses_the_aggregate_artifact_budget():
    inputs = {"cache.py": "abc"}
    value = report(inputs)
    long_id = "tests/test_consumer.py::test_tenant[" + "scenario" * 700 + "]"
    value["tests"][1] = long_id
    value["files"][0].update(executing=[1], selected=[0])
    result = audit_selection(value, inputs)
    assert result["status"] == "reported_gap"
    assert result["findings"][0]["omitted_tests"] == [long_id]


def test_input_inventory_environment_and_missing_scope_never_imply_safety():
    inputs = {"cache.py": "abc"}
    value = report(inputs)
    value["files"][0]["selected"] = value["tests"]
    assert audit_selection(value, inputs)["status"] == "unverified"
    assert audit_selection(value, inputs, {"python": "other"})["environment_status"] == "mismatched"
    assert audit_selection(value, inputs, value["environment"])["status"] == "reported_passed"
    assert audit_selection(value, inputs | {"consumer.py": "new"})["status"] == "stale"
    assert audit_selection(value, {"cache.py": "changed"})["status"] == "stale"
    missing = audit_selection(value, inputs, value["environment"], ["consumer.py"])
    assert missing["status"] == "unverified" and missing["unobserved_paths"] == ["consumer.py"]
    for altered in (None, [], {"version": True}, value | {"files": [value["files"][0]] * 2}):
        result = audit_selection(altered, inputs)
        assert result["status"] in {"invalid", "not_supplied"}


def test_application_ci_report_is_captured_and_stale_after_any_consumer_edit(tmp_path):
    for path in ("cache.py", "tests/test_local.py", "tests/test_consumer.py"):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("value = 1\n")
    app = Application(tmp_path)
    app.scan()
    baseline = app.prepare_change("Change cache")
    value = report(copy.deepcopy(app._current().snapshot.files))
    target = tmp_path / REPORT_PATH
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(value))
    app.scan()
    prepared = app.prepare_change("Change cache", continue_task=True)
    assert prepared["delivery"]["selection_evidence"]["status"] == "reported_gap"
    assert prepared["delivery"]["status"] == "not_reviewed"  # No model review required.
    assert (
        app.analysis_request("delivery")["packet"]["delivery_context"]["test_selection"]["status"]
        == "reported_gap"
    )
    plan = app.ci_plan(changed_paths=["cache.py"])
    assert plan["selection_evidence"]["findings"][0]["omitted_count"] == 1
    assert not plan["execution_ready"]
    (tmp_path / "tests/test_consumer.py").write_text("value = 2\n")
    app.scan()
    result = app.validate_change(baseline_id=baseline["baseline_id"])
    assert result["ci_selection"]["status"] == "stale"
    assert (
        hashlib.sha256(target.read_bytes()).hexdigest()
        == app._current().snapshot.files[REPORT_PATH]
    )


def test_configured_planner_never_uses_a_gap_as_authority_to_skip_consumers(tmp_path):
    (tmp_path / "cache.py").write_text("value = 1\n")
    data = tmp_path / "anaxigraph"
    data.mkdir()
    (data / "ci-policy.json").write_text(
        json.dumps(
            {
                "version": 1,
                "targets": [
                    {"id": "local", "command": "pytest tests/cache", "inputs": ["cache.py"]}
                ],
            }
        )
    )
    app = Application(tmp_path)
    app.scan()
    value = report(dict(app._current().snapshot.files))
    (data / "ci-evidence.json").write_text(json.dumps(value))
    app.scan()
    result = app.ci_plan(changed_paths=["cache.py"], runtime_context=value["environment"])
    assert result["status"] == "selection_review_required" and not result["execution_ready"]
    assert result["selection_evidence"]["status"] == "reported_gap"
    for invalid_source in ("null", "[" * 1500 + "0" + "]" * 1500):
        (data / "ci-evidence.json").write_text(invalid_source)
        app.scan()
        invalid = app.ci_plan(changed_paths=["cache.py"])
        assert invalid["selection_evidence"]["status"] == "invalid"
        assert not invalid["execution_ready"]
