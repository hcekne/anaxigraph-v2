"""Efficiency cannot compensate for unsafe selection or missing verification evidence."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "feature_drift_report", Path(__file__).with_name("report.py")
)
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


def suite():
    return {"returncode": 0, "tests": 3, "passed": 3}


def hidden(names):
    return {
        "collection_error": False,
        "by_file": {name: {"passed": 1, "total": 1} for name in names},
    }


def records():
    rows = [
        {
            "step": n,
            "hidden": hidden(f"step_{k:02d}" for k in range(1, n + 1)),
            "own_suite": suite(),
        }
        for n in range(1, 21)
    ]
    rows[-1]["ci"] = {
        "selector": "ok",
        "selector_failures": 0,
        "coverage_run": {"returncode": 0},
        "tests_total": 3,
        "mean_selected_fraction": 0.3,
        "mutants": {"planted": 4, "caught_by_suite": 2, "missed_by_selection": 0},
    }
    probes = [
        {
            "probe": name,
            "hidden_probe": hidden([f"probe_{n}"]),
            "hidden_regression": hidden(f"step_{k:02d}" for k in range(1, 21)),
            "own_suite": suite(),
            "ci_selected": 1,
            "ci_total": 3,
        }
        for n, name in enumerate(("p1-geolookup", "p2-notes", "p3-timeouts"), 1)
    ]
    return rows, probes


def arms():
    control = {
        "last_step": 20,
        "replicates": [0, 1, 2],
        "ci_measured_steps": {str(n): [20] for n in range(3)},
        "verification_status": "passed",
        "mutants_missed_by_selection": 0,
        "final_cumulative_rate": 1.0,
        "probe_pass": "3/3",
        "late_existing_files_edited_per_step": 10,
        "probe_existing_files_edited": 10,
        "final_ci_selected_fraction": 1.0,
        "agent_minutes_total": 10,
        "analysis_minutes_total": 0,
    }
    treatment = copy.deepcopy(control) | {
        "late_existing_files_edited_per_step": 1,
        "probe_existing_files_edited": 1,
        "final_ci_selected_fraction": 0.1,
    }
    return {"charter": control, "anaxigraph": treatment}


def test_complete_checks_and_sparse_recorded_cadence_qualify():
    rows, probes = records()
    result = report.verification(rows, probes)
    assert result == {
        "status": "passed",
        "failures": [],
        "incomplete": [],
        "ci_measured_steps": [20],
    }
    assert report.decide(arms())["verdict"] == "treatment better"


@pytest.mark.parametrize(
    "fault",
    [
        "missed",
        "own_failure",
        "hidden_failure",
        "coverage_failure",
        "selector_failure",
        "missing_step",
        "duplicate_step",
        "missing_probe",
        "missing_mutants",
        "no_mutants",
        "none_caught",
        "missing_coverage",
        "missing_own",
        "missing_final_ci",
    ],
)
def test_unsafe_or_incomplete_verification_cannot_earn_benefit(fault):
    rows, probes = records()
    ci = rows[-1]["ci"]
    if fault == "missed":
        ci["mutants"]["missed_by_selection"] = 1
    elif fault == "own_failure":
        rows[0]["own_suite"]["returncode"] = 1
    elif fault == "hidden_failure":
        rows[0]["hidden"]["by_file"]["step_01"]["passed"] = 0
    elif fault == "coverage_failure":
        ci["coverage_run"]["returncode"] = 124
    elif fault == "selector_failure":
        ci["selector_failures"] = 1
    elif fault == "missing_step":
        rows.pop(4)
    elif fault == "duplicate_step":
        rows.append(copy.deepcopy(rows[0]))
    elif fault == "missing_probe":
        probes.pop()
    elif fault == "missing_mutants":
        ci.pop("mutants")
    elif fault == "no_mutants":
        ci["mutants"]["planted"] = 0
    elif fault == "none_caught":
        ci["mutants"]["caught_by_suite"] = 0
    elif fault == "missing_coverage":
        ci.pop("coverage_run")
    elif fault == "missing_own":
        rows[0].pop("own_suite")
    elif fault == "missing_final_ci":
        rows[-1].pop("ci")
    result = report.verification(rows, probes)
    assert result["status"] != "passed"
    summary = arms()
    summary["anaxigraph"]["verification_status"] = result["status"]
    assert report.decide(summary)["verdict"] != "treatment better"


@pytest.mark.parametrize(
    "fault", ["single_pair", "unmatched", "duplicate", "unknown", "cadence", "legacy"]
)
def test_incomplete_comparisons_keep_metrics_but_cannot_win(fault):
    summary = arms()
    treatment = summary["anaxigraph"]
    if fault == "single_pair":
        treatment["replicates"] = [0]
    elif fault == "unmatched":
        treatment["replicates"] = [1, 2, 3]
    elif fault == "duplicate":
        treatment["replicates"] = [0, 0, 2]
    elif fault == "unknown":
        treatment["replicates"] = [None, 1, 2]
    elif fault == "cadence":
        treatment["ci_measured_steps"]["0"] = [6, 20]
    elif fault == "legacy":
        treatment.pop("verification_status")
    assert report.decide(summary)["verdict"] == "incomplete"
    assert treatment["final_ci_selected_fraction"] == 0.1


def test_zero_known_misses_and_other_advantages_cannot_hide_unsafe_selection():
    summary = arms()
    for arm in summary.values():
        arm["mutants_missed_by_selection"] = 1
    decision = report.decide(summary)
    assert decision["verdict"] == "not shown"
    assert not decision["criteria"]["cheaper_safe_ci"]
    assert not decision["criteria"]["safety_qualified"]


def test_unknown_timing_does_not_become_zero_cost():
    summary = arms()
    summary["anaxigraph"]["analysis_minutes_total"] = None
    decision = report.decide(summary)
    assert decision["time_ratio"] is None
    assert decision["within_cost_budget"] is None


def test_chain_and_arm_summary_preserve_unknown_mutants_and_incomplete_individual_chain(tmp_path):
    rows, probes = records()
    rows.pop(4)  # A final step 20 is insufficient when an earlier request is absent.
    rows[-1]["ci"]["mutants"].pop("missed_by_selection")
    for row in rows:
        row["hidden"].update(failed=[], passed=[])
        row["static"] = dict.fromkeys(
            ("prod_loc", "duplication", "import_cycles", "max_complexity", "largest_module_loc"), 1
        )
        row["diff"] = dict.fromkeys(
            ("prod_modified_files", "prod_added_files", "prod_lines_added", "prod_lines_deleted"), 1
        )
        row["own_suite"]["seconds"] = 1
    for row in probes:
        row["diff"] = dict.fromkeys(
            ("prod_modified_files", "prod_added_files", "prod_lines_added", "prod_lines_deleted"), 1
        )
        row["agent"] = {}
    (tmp_path / "chain.json").write_text(json.dumps({"arm": "anaxigraph", "replicate": 0}))
    (tmp_path / "steps.jsonl").write_text("\n".join(map(json.dumps, rows)))
    (tmp_path / "probes.jsonl").write_text("\n".join(map(json.dumps, probes)))
    chain = report.chain_summary(tmp_path)
    summary = report.arm_summary([chain])
    assert summary["last_step"] == 20
    assert summary["verification_status"] == "incomplete"
    assert summary["mutants_missed_by_selection"] is None
    assert summary["final_ci_selected_fraction"] == 0.3
