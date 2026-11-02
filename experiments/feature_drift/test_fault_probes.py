"""Run real temporary sources and subprocess assertions, including the report writer."""

import json
from pathlib import Path

import fault_probes


def test_local_selection_report_fails_when_consumer_contracts_are_omitted(tmp_path):
    output = tmp_path / "reports" / "local.json"
    assert fault_probes.main(["--output", str(output)]) == 1
    result = json.loads(output.read_text())
    assert result["baseline"]["exit_code"] == 0
    assert result["baseline"]["tests"] == 9
    assert result["counts"] == {
        "injected": 7,
        "reference_detected": 7,
        "selected_detected": 0,
        "widened_detected": 7,
        "selection_misses": 7,
    }
    assert result["status"] == "unsafe_selection"
    assert result["provider_calls"] == 0
    assert result["seconds"] > 0
    for row in result["faults"]:
        assert row["reference"]["failed_tests"]
        assert row["selected"]["tests"] == 2
        assert row["selected"]["exit_code"] == 0
        assert row["reference"]["seconds"] > 0
        assert row["path"] in {"cache.py", "breaker.py"}
    assert not (tmp_path / "cache.py").exists()


def test_widened_selection_detects_every_sampled_reference_fault(tmp_path):
    output = tmp_path / "widened.json"
    assert fault_probes.main(["--selection", "widened", "--output", str(output)]) == 0
    result = json.loads(output.read_text())
    assert result["counts"]["selection_misses"] == 0
    assert result["counts"]["selected_detected"] == result["counts"]["reference_detected"] == 7
    assert result["status"] == "sampled_faults_detected"
    assert all(row["selected"]["failed_tests"] for row in result["faults"])


def test_fixture_runner_never_touches_frozen_trial_repositories(monkeypatch, tmp_path):
    roots = set()
    original = fault_probes._run

    def recording(root, scope):
        roots.add(root)
        assert "anaxigraph-fault-probes-" in root.name
        return original(root, scope)

    monkeypatch.setattr(fault_probes, "_run", recording)
    fault_probes.qualify("local")
    assert len(roots) == 1
    assert all(not Path(root).exists() for root in roots)
