"""Resume orchestration with synthetic transport; no model/provider calls."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter

import pytest

from . import run


def instrument_trial(tmp_path, monkeypatch):
    source = tmp_path / "runtime-source"
    source.mkdir()
    (source / "version.py").write_text("VERSION = 1\n")
    preparations, coding, accepted = [], [], Counter()
    original_arm = run.run_arm
    identity = {"available": True, "resolved_path": "/synthetic/client", "version": "fixture"}
    monkeypatch.setattr(run, "client_identity", lambda config: dict(identity))

    def preflight(config):
        proof = {
            "status": "passed",
            "evidence_kind": "synthetic",
            "runtime": str(config.runtime),
            "runtime_sha256": hashlib.sha256(config.runtime.read_bytes()).hexdigest(),
            "coding": {"tokens": None, "cost_usd": None},
        }
        run.write(config.state / "preflight.json", proof)
        return proof

    def prepare(config, root, destination):
        preparations.append((config.state.name, destination.name))
        run.write(destination / "workflow-config.private.json", run.workflow_configuration(config))
        receipt_path = destination / "accepted-batch.private.json"
        if not receipt_path.exists():
            accepted[(config.state.name, destination.name)] += 1
            run.write(receipt_path, {"status": "accepted", "model_call": "synthetic"})
        failure = destination / "failed-batch.private.json"
        if (
            config.state.name == "interrupted"
            and destination.name == "checkout-1"
            and not failure.exists()
        ):
            run.write(failure, {"status": "failed", "cost_usd": None, "evidence_kind": "synthetic"})
            raise RuntimeError("synthetic provider failure before coding")
        result = {
            "reviews": [],
            "model_receipts": [],
            "quality_charter": [],
            "baseline_digest": run.digest(run.snapshot(root)),
        }
        result["frozen_digest"] = run.digest(result)
        run.write(destination / "advice.private.json", result)
        return result

    def arm(config, root, name, advice, arm, sample_seed, charter=None):
        coding.append((config.state.name, root.parent.name, arm))
        synthetic = copy.copy(config)
        synthetic.live = False
        return original_arm(synthetic, root, name, advice, arm, sample_seed, charter)

    monkeypatch.setattr(run, "preflight", preflight)
    monkeypatch.setattr(run, "freeze_advice", prepare)
    monkeypatch.setattr(run, "run_arm", arm)

    def arguments(state, resume=False):
        args = [
            "--live",
            "--agent",
            "synthetic-fixture",
            "--runtime",
            "/bin/true",
            "--runtime-source-root",
            str(source),
            "--state",
            str(state),
            "--scenarios",
            "checkout",
            "--replicates",
            "2",
            "--stages",
            "2",
            "--random-seed",
            "77123",
        ]
        return args + (["--resume-preparation"] if resume else [])

    def invoke(state, resume=False):
        monkeypatch.setattr("sys.argv", ["run", *arguments(state, resume)])
        return run.main()

    return invoke, arguments, preparations, coding, accepted, identity


def test_recovery_preserves_completed_pair_and_matches_uninterrupted_randomization(
    tmp_path, monkeypatch
):
    invoke, _, preparations, coding, accepted, _ = instrument_trial(tmp_path, monkeypatch)
    state = tmp_path / "interrupted"
    assert invoke(state) == 1
    original = json.loads((state / "run.private.json").read_text())
    assert original["status"] == "incomplete" and len(original["pairs"]) == 1
    assert original["evidence_kind"] == "synthetic"
    prefix = {
        str(path.relative_to(state)): path.read_bytes()
        for directory in (state / "checkout-0", state / "blind-review")
        for path in directory.rglob("*")
        if path.is_file()
    }
    # The original coordinator wrote the assignment map only after all pairs.
    (state / "blind-map.private.json").unlink()
    assert invoke(state, resume=True) == 0
    recovered = json.loads((state / "run.private.json").read_text())
    assert recovered["status"] == "completed" and recovered["evidence_kind"] == "synthetic"
    assert len(recovered["pairs"]) == 2
    assert recovered["pairs"][0] == original["pairs"][0]
    assert all((state / path).read_bytes() == data for path, data in prefix.items())
    assert preparations.count(("interrupted", "checkout-0")) == 1
    assert preparations.count(("interrupted", "checkout-1")) == 2
    assert Counter(coding) == Counter(
        ("interrupted", f"checkout-{replicate}", arm)
        for replicate in range(2)
        for arm in ("control", "treatment")
    )
    assert all(count == 1 for count in accepted.values())
    assert len(recovered["administration"]) == 2
    assert recovered["preparation_recoveries"][0]["completed_pairs_preserved"] == 1
    failure = json.loads((state / "recovery-1.previous-run.private.json").read_text())
    assert failure["error"] == "synthetic provider failure before coding"
    assert (state / "checkout-1/failed-batch.private.json").is_file()

    baseline = tmp_path / "uninterrupted"
    assert invoke(baseline) == 0
    straight = json.loads((baseline / "run.private.json").read_text())
    assert [pair["hidden_sample_seed"] for pair in recovered["pairs"]] == [
        pair["hidden_sample_seed"] for pair in straight["pairs"]
    ]
    assert json.loads((state / "blind-map.private.json").read_text()) == json.loads(
        (baseline / "blind-map.private.json").read_text()
    )


def test_recovery_refuses_modified_completed_evidence_and_pending_coding(tmp_path, monkeypatch):
    invoke, _, _, coding, _, _ = instrument_trial(tmp_path, monkeypatch)
    state = tmp_path / "interrupted"
    assert invoke(state) == 1
    before = (state / "run.private.json").read_bytes()
    label = next((state / "blind-review").glob("candidate-*"))
    files = [
        state / "checkout-0/control-evidence/task-0/result.private.json",
        state / "checkout-0/control-evidence/task-0/baseline.private.json",
        state / "checkout-0/advice.private.json",
        state / "checkout-0/control/compute.py",
        label / "compute.py",
        label / "diffs/task-0.patch",
        label / "tasks.json",
    ]
    for path in files:
        original = path.read_bytes()
        path.write_text("{}" if path.suffix == ".json" else "modified\n")
        assert invoke(state, resume=True) == 1
        assert (state / "run.private.json").read_bytes() == before
        path.write_bytes(original)
    task = state / "checkout-1/treatment-evidence/task-0"
    task.mkdir(parents=True)
    assert invoke(state, resume=True) == 1
    assert (state / "run.private.json").read_bytes() == before
    task.rmdir()
    unexpected = state / "unexpected-pair/control-evidence/task-0"
    unexpected.mkdir(parents=True)
    assert invoke(state, resume=True) == 1
    assert (state / "run.private.json").read_bytes() == before
    unexpected.rmdir()
    changed = json.loads(before)
    changed["pairs"][0]["analysis_model_receipts"]["treatment"] = [{"status": "invented"}]
    run.write(state / "run.private.json", changed)
    changed_bytes = (state / "run.private.json").read_bytes()
    assert invoke(state, resume=True) == 1
    assert (state / "run.private.json").read_bytes() == changed_bytes
    assert len(coding) == 2
    assert not (state / "recovery-1.previous-run.private.json").exists()


@pytest.mark.parametrize("change", ["client", "configuration", "runtime", "source"])
def test_recovery_refuses_changed_frozen_inputs_after_completed_pair(tmp_path, monkeypatch, change):
    invoke, arguments, _, coding, _, identity = instrument_trial(tmp_path, monkeypatch)
    state = tmp_path / "interrupted"
    assert invoke(state) == 1
    before = (state / "run.private.json").read_bytes()
    args = arguments(state, resume=True)
    if change == "client":
        identity["version"] = "different-client"
    elif change == "configuration":
        args[args.index("--stages") + 1] = "1"
    elif change == "runtime":
        proof = json.loads((state / "preflight.json").read_text())
        proof["runtime_sha256"] = "changed"
        run.write(state / "preflight.json", proof)
    else:
        (tmp_path / "runtime-source/version.py").write_text("VERSION = 2\n")
    monkeypatch.setattr("sys.argv", ["run", *args])
    assert run.main() == 1
    assert (state / "run.private.json").read_bytes() == before
    assert len(coding) == 2
