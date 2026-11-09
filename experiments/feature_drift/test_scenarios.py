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
    track = scenarios.draft(track_id)
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
    track = scenarios.draft("chronoweave")
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
        (track, scenarios.describe(track)["status"]) for track in scenarios.TRACK_IDS
    ]
    assert rows[0]["status"] == "executable"


@pytest.mark.parametrize("track_id", scenarios.TRACK_IDS[1:])
def test_specification_only_execution_stops_before_clients_or_state_creation(
    track_id, monkeypatch, tmp_path
):
    _drafts_only(monkeypatch, tmp_path / "catalog")
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


def test_real_runtime_preflight_exercises_fail_closed_validation_without_models():
    runner.preflight_runtime(SimpleNamespace(runtime=runner.DEFAULT_RUNTIME))


def test_old_runtime_is_rejected_before_state_or_paid_sessions(monkeypatch, tmp_path):
    executable = tmp_path / "old-runtime"
    executable.write_text(
        f"#!{sys.executable}\nimport json\nprint(json.dumps({{'snapshot': {{'id': 'old'}}}}))\n"
    )
    executable.chmod(0o755)
    state = tmp_path / "state"
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "--state", str(state), "--arms", "anaxigraph", "--runtime", str(executable)],
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported runtime reached coding or trial publication")

    monkeypatch.setattr(runner, "provenance", forbidden)
    monkeypatch.setattr(runner, "run_agent", forbidden)
    with pytest.raises(SystemExit) as rejected:
        runner.main()
    assert rejected.value.code == 2
    assert not state.exists()


@pytest.mark.parametrize(
    "changed",
    [
        "requests_sha256",
        "oracle_sha256",
        "runtime_version",
        "evaluation_resource_policy",
        "missing",
    ],
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
            "evaluation_resource_policy",
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
    elif changed in {"runtime_version", "evaluation_resource_policy"}:
        metadata[changed] = "new"
    else:
        metadata["scenario_identity"][changed] = "changed"
    path.write_text(json.dumps(metadata))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="incompatible frozen inputs"):
        runner.validate_resume(config, tmp_path)
    assert path.read_bytes() == before


def _track_folder(base: Path, *, requests: int = 21) -> None:
    base.mkdir(parents=True)
    (base / "track.json").write_text(json.dumps({
        "id": "chronoweave", "name": "ChronoWeave", "version": 1, "product": "ChronoWeave",
        "ci_from_step": 5, "inline_probes": {"7": "P1", "14": "P2", "20": "P3"},
        "adaptations": ["zstd -> gzip"],
    }))  # fmt: skip
    for folder in ("seed", "requests", "hidden"):
        (base / folder).mkdir()
    (base / "seed" / "run.json").write_text('{"command": []}')
    for number in range(requests):
        (base / "requests" / f"{number:02d}-request.md").write_text(f"# Request {number}")
        (base / "hidden" / f"step_{number:02d}.py").write_text("")


def _drafts_only(monkeypatch, base: Path) -> None:
    """A catalog holding only the imported specifications, independent of built folders."""
    (base / "tracks").mkdir(parents=True)
    for name in ("chronoweave.json", "aetherloom.json"):
        (base / "tracks" / name).write_text((scenarios.HERE / "tracks" / name).read_text())
    monkeypatch.setattr(scenarios, "HERE", base)


def test_complete_track_folder_becomes_executable_with_inline_probes(monkeypatch, tmp_path):
    _drafts_only(monkeypatch, tmp_path)
    _track_folder(tmp_path / "tracks" / "chronoweave", requests=20)
    assert scenarios.describe("chronoweave")["status"] == "specification_only"
    (tmp_path / "tracks" / "chronoweave" / "requests" / "20-request.md").write_text("# Request 20")
    (tmp_path / "tracks" / "chronoweave" / "hidden" / "step_20.py").write_text("")
    track = scenarios.describe("chronoweave")
    required = scenarios.requirements(track)
    assert track["status"] == "executable" and track["product"] == "ChronoWeave"
    assert required["steps"] == list(range(21))
    assert required["inline_probes"] == [
        {"step": 7, "id": "P1"}, {"step": 14, "id": "P2"}, {"step": 20, "id": "P3"}
    ]  # fmt: skip
    assert required["post_history_probes"] == []
    assert required["comparison_rule"] == "chronoweave-v1"
    paths = scenarios.layout(track)
    assert paths["ci_from_step"] == 5 and len(paths["requests"]) == 21
    assert scenarios.identity(track)["oracle_sha256"]


def test_reference_agent_checks_out_the_tagged_step_and_keeps_requests(tmp_path):
    source, repo = tmp_path / "reference", tmp_path / "repo"
    for path in (source, repo / "docs" / "requests"):
        path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "app.py").write_text("step = 3\n")
    git = ["git", "-C", str(source), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "s3"], check=True)
    subprocess.run([*git, "tag", "step-03"], check=True)
    (repo / "stale.py").write_text("old")
    (repo / "docs" / "requests" / "03.md").write_text("request")
    result = runner.checkout_reference(source, "step-03", repo, 0.0)
    assert result["status"] == "reference"
    assert (repo / "app.py").read_text() == "step = 3\n"
    assert not (repo / "stale.py").exists() and (repo / "docs" / "requests" / "03.md").exists()
    assert runner.checkout_reference(source, "repair-1", repo, 0.0)["status"] == "noop"


def test_agent_timeout_defaults_to_the_track_limit():
    limits = {
        track: scenarios.layout(scenarios.describe(track))["agent_timeout"]
        for track in scenarios.TRACK_IDS
    }
    assert limits["switchboard"] == 5400
    for track in scenarios.TRACK_IDS[1:]:
        if scenarios.describe(track)["status"] == "executable":
            assert limits[track] == 7200
