"""Catalog and harness qualification without coding-model calls."""

from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
runner, scenarios = (
    importlib.import_module("experiments.feature_drift." + name) for name in ("runner", "scenarios")
)


@pytest.mark.parametrize("track_id", scenarios.TRACK_IDS[1:])
def test_imported_specifications_keep_all_requests_and_inline_probes_without_outcomes(track_id):
    track = scenarios.describe(track_id)
    assert [step["number"] for step in track["steps"]] == list(range(21))
    assert [
        (step["number"], step["probe_id"]) for step in track["steps"] if "probe_id" in step
    ] == [(7, "P1"), (14, "P2"), (20, "P3")]
    assert len(track["oracle_ideas"]) == 3
    assert track["status"] == "specification_only"
    assert track["evidence_kind"] == "unexecuted_specification"
    assert scenarios.identity(track)["oracle_sha256"] is None
    assert not track["post_history_probes"]
    assert not any(
        f'"{key}":' in json.dumps(track) for key in ("codingSeconds", "arms", "empiricalSymptom")
    )


@pytest.mark.parametrize("tamper", ["missing", "duplicate", "probe", "outcomes", "ready"])
def test_specification_validation_rejects_missing_requests_and_fabricated_readiness(tamper):
    track = scenarios.describe("chronoweave")
    if tamper == "missing":
        track["steps"].pop()
    elif tamper == "duplicate":
        track["steps"][1] = track["steps"][0]
    elif tamper == "probe":
        track["steps"][7]["probe_id"] = "P2"
    elif tamper == "outcomes":
        track["steps"][0]["arms"] = {"anaxigraph": {"wins": True}}
    else:
        track["status"] = "executable"
    with pytest.raises(ValueError):
        scenarios.validate_specification(track)


def test_switchboard_keeps_twenty_requests_and_three_independent_later_probes():
    track = scenarios.describe()
    required = scenarios.requirements(track)
    assert required["steps"] == list(range(1, 21))
    assert required["inline_probes"] == []
    assert required["post_history_probes"] == ["p1-geolookup", "p2-notes", "p3-timeouts"]
    assert required["comparison_rule"] == "switchboard-v1"
    assert scenarios.identity(track)["oracle_sha256"]


def test_cumulative_acceptance_includes_initial_design_and_inline_probe_history():
    required = scenarios.requirements(scenarios.describe("aetherloom"))
    assert scenarios.acceptance_files(required, 0) == ["step_00.py"]
    assert scenarios.acceptance_files(required, 8) == [f"step_{n:02d}.py" for n in range(9)]
    assert "step_07.py" in scenarios.acceptance_files(required, 20)


def test_request_seed_and_oracle_changes_have_distinct_frozen_identities(monkeypatch, tmp_path):
    track = scenarios.describe()
    root = tmp_path / "scenario"
    for name, content in {
        "seed/app.py": "original",
        "hidden/step_01.py": "assert 1",
        "hub.py": "pass",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.setattr(scenarios, "HERE", tmp_path)
    original = scenarios.identity(track)
    edited = copy.deepcopy(track)
    edited["steps"][0]["prompt"] += "\nNew behavior"
    assert scenarios.identity(edited)["requests_sha256"] != original["requests_sha256"]
    assert scenarios.identity(edited)["scenario_sha256"] != original["scenario_sha256"]
    (root / "seed/app.py").write_text("changed")
    seed_changed = scenarios.identity(track)
    assert seed_changed["scenario_sha256"] != original["scenario_sha256"]
    assert seed_changed["oracle_sha256"] == original["oracle_sha256"]
    (root / "hidden/step_01.py").write_text("assert 2")
    assert scenarios.identity(track)["oracle_sha256"] != original["oracle_sha256"]


def test_dot_directories_are_inputs_but_explicit_runtime_caches_are_not(tmp_path):
    for relative in (
        ".github/workflows/test.yml",
        ".runtime/schema.json",
        "app.py",
        ".venv/cache.py",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content")
    assert set(scenarios.file_hashes(tmp_path)) == {
        ".github/workflows/test.yml",
        ".runtime/schema.json",
        "app.py",
    }


def cli(*args):
    return subprocess.run(
        [sys.executable, str(Path(runner.__file__)), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("track_id", scenarios.TRACK_IDS)
def test_catalog_export_and_describe_execute_end_to_end_without_model_or_state(track_id, tmp_path):
    output = tmp_path / "catalog.json"
    result = cli("--track", track_id, "--describe-track", "--export-track", output)
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text())
    assert json.loads(result.stdout) == payload
    assert payload["scenario_identity"] == scenarios.identity(payload["track"])
    assert list(tmp_path.iterdir()) == [output]
    before = output.read_bytes()
    assert cli("--track", track_id, "--export-track", output).returncode != 0
    assert output.read_bytes() == before


def test_list_tracks_names_readiness_without_executing_them():
    result = cli("--list-tracks")
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    assert [(row["id"], row["status"]) for row in rows] == [
        ("switchboard", "executable"),
        ("chronoweave", "specification_only"),
        ("aetherloom", "specification_only"),
    ]


@pytest.mark.parametrize("track_id", scenarios.TRACK_IDS[1:])
def test_specification_only_execution_stops_before_clients_or_state_creation(
    track_id, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        sys, "argv", ["runner", "--track", track_id, "--state", str(tmp_path / "run")]
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Draft specification reached executable benchmark code")

    monkeypatch.setattr(runner, "provenance", forbidden)
    monkeypatch.setattr(runner, "run_agent", forbidden)
    with pytest.raises(SystemExit) as result:
        runner.main()
    assert result.value.code == 2
    assert not (tmp_path / "run").exists()


def test_duplicate_arms_cannot_start_concurrent_writers(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys, "argv", ["runner", "--state", str(tmp_path / "run"), "--arms", "bare,bare"]
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Duplicate writers reached benchmark execution")

    monkeypatch.setattr(runner, "provenance", forbidden)
    with pytest.raises(SystemExit) as result:
        runner.main()
    assert result.value.code == 2
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize(
    "changed", ["requests_sha256", "oracle_sha256", "runtime_version", "missing"]
)
def test_resume_requires_exact_frozen_scenario_and_runtime_inputs(changed, tmp_path):
    provenance = dict.fromkeys(
        (
            "model",
            "effort",
            "agent",
            "runtime",
            "runtime_version",
            "runtime_commit",
            "harness_sha256",
            "codex_version",
            "review_steps",
            "review_kinds",
            "workflow_config_sha256",
            "ci_repair_attempts",
            "ci_gate_protocol",
            "measurement_settings",
        ),
        "frozen",
    )
    provenance["scenario_identity"] = scenarios.identity(scenarios.describe())
    config = SimpleNamespace(
        provenance=provenance, scenario=scenarios.requirements(scenarios.describe())
    )
    metadata = {**copy.deepcopy(provenance), "scenario": config.scenario}
    path = tmp_path / "chain.json"
    path.write_text(json.dumps(metadata))
    runner.validate_resume(config, tmp_path)
    if changed == "missing":
        metadata.pop("scenario_identity")
    elif changed == "runtime_version":
        metadata[changed] = "new"
    else:
        metadata["scenario_identity"][changed] = "changed"
    path.write_text(json.dumps(metadata))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="incompatible frozen inputs"):
        runner.validate_resume(config, tmp_path)
    assert path.read_bytes() == before
