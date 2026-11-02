"""The retrospective reader preserves frozen bytes and keeps missing evidence unknown."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import audit
import pytest


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture_state(tmp_path):
    state = tmp_path / "frozen"
    chain = state / "anaxigraph-r0"
    save(chain / "chain.json", {"arm": "anaxigraph"})
    (chain / "steps.jsonl").write_text(json.dumps({"step": 20, "static": {"prod_files": 2}}) + "\n")
    save(
        chain / "eval/step-20/ci.json",
        {
            "tests_total": 10,
            "coverage_run": {"returncode": 0, "seconds": 12.5},
            "selector_failures": 0,
            "mean_selected_fraction": 0.1,  # Intentionally wrong aggregate; audit recomputes raw counts.
            "per_file": [
                {"file": "cache.py", "selected": 3, "executing": 4, "executing_selected": 2},
                {"file": "breaker.py", "selected": 5, "executing": 2, "executing_selected": 2},
            ],
            "mutants": {"planted": 2, "caught_by_suite": 2, "missed_by_selection": 0},
        },
    )
    (chain / "repo").mkdir()
    (chain / "repo/sentinel.py").write_text("raise RuntimeError('audit must never execute this')\n")
    (chain / "logs").mkdir()
    events = []
    for identity, status in (("one", "in_progress"), ("one", "completed"), ("two", "completed")):
        result = (
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"architecture": {"status": "no_current_material_proposals"}}
                        ),
                    }
                ]
            }
            if status == "completed"
            else None
        )
        events.append(
            {
                "type": "item.updated",
                "item": {
                    "id": identity,
                    "type": "mcp_tool_call",
                    "server": "anaxigraph",
                    "tool": "assess_plan",
                    "arguments": {"plan": []},
                    "result": result,
                    "status": status,
                },
            }
        )
    events.append(
        {
            "item": {
                "id": "three",
                "type": "mcp_tool_call",
                "server": "anaxigraph",
                "tool": "inspect",
                "status": "failed",
                "error": "bad identity",
            }
        }
    )
    (chain / "logs/step-20.jsonl").write_text("".join(json.dumps(item) + "\n" for item in events))
    return state


def frozen_bytes(state):
    return {
        str(path.relative_to(state)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in state.rglob("*")
        if path.is_file()
    }


def test_cli_reads_real_saved_counts_and_preserves_every_original_byte(tmp_path):
    state = fixture_state(tmp_path)
    before = frozen_bytes(state)
    output = tmp_path / "evidence/retrospective.json"
    result = subprocess.run(
        [sys.executable, str(Path(audit.__file__)), "--state", str(state), "--output", str(output)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert frozen_bytes(state) == before
    report = json.loads(output.read_text())
    assert report["input_unchanged"] and report["model_calls"] == 0
    assert report["candidate_execution"] == "not_performed"
    arm = report["arms"]["anaxigraph"]
    ci = arm["chains"][0]["checkpoints"][0]
    assert (ci["mean_selected_tests"], ci["mean_selected_percent"]) == (4, 40)
    assert (ci["production_files"], ci["test_denominator"]) == (2, 10)
    assert ci["omitted_execution_pairs"] == 2
    assert ci["files_with_omitted_consumers"] == 1
    assert ci["executing_recall"] == 4 / 6
    assert ci["coverage_completeness"] == "observed_gap"
    assert ci["sampled_mutants"]["status"] == "caught_all_reference_detected_samples"
    assert ci["exact_omitted_test_ids"] is None
    assert ci["historical_escaped_bug"] == "not_established"
    assert ci["selected_suite_seconds"] is None
    assert ci["coverage_run_seconds"] == 12.5
    tools = arm["chains"][0]["tools"]
    assert tools["tool_calls"] == 3  # A start/update event is not an extra invocation.
    assert tools["tool_errors"] == 1
    assert tools["no_material_assessments"] == 2
    assert tools["repeated_result_digest_calls"] == 1
    assert tools["response_payload_bytes"] > 0
    assert tools["cost_usd"] is None
    original_output = output.read_bytes()
    repeated = subprocess.run(
        [sys.executable, str(Path(audit.__file__)), "--state", str(state), "--output", str(output)],
        capture_output=True,
        text=True,
    )
    assert repeated.returncode == 2 and "already exists" in repeated.stderr
    assert output.read_bytes() == original_output
    assert frozen_bytes(state) == before


def test_output_cannot_enter_state_or_clobber_an_existing_symlink(tmp_path):
    state = fixture_state(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        audit.write_new_output(state, state / "audit.json")
    output = tmp_path / "link.json"
    output.symlink_to(tmp_path / "missing.json")
    with pytest.raises(ValueError, match="exists"):
        audit.write_new_output(state, output)
    assert not (tmp_path / "missing.json").exists()
    assert not (state / "audit.json").exists()


def test_absent_inventory_and_unsampled_faults_remain_unknown():
    result = audit.checkpoint({"tests_total": 10}, {"step": 1}, "step-01")
    assert result["omitted_execution_pairs"] is None
    assert result["files_with_omitted_consumers"] is None
    assert result["mean_selected_tests"] is None
    assert result["coverage_completeness"] == "unknown"
    assert result["sampled_mutants"]["status"] == "unknown"
    observed = audit.checkpoint(
        {
            "per_file": [
                {"file": "one.py", "selected": 1, "executing": 1, "executing_selected": 1}
            ],
            "coverage_run": {"returncode": 1},
            "selector_failures": 0,
        },
        {"static": {"prod_files": 1}},
        "step-01",
    )
    assert observed["coverage_completeness"] == "unknown"
