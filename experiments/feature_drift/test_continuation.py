"""Exploratory recovery preserves history, costs, source and the original safety bar."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import continuation  # noqa: E402
import report  # noqa: E402
import runner as driver  # noqa: E402


@pytest.fixture
def fork(tmp_path):
    source = tmp_path / "frozen"
    chain = source / "bare-r0"
    (chain / "repo/.git").mkdir(parents=True)
    (chain / "repo/.git/HEAD").write_text("source-head")
    (chain / "repo/.anaxigraph-v2").mkdir()
    (chain / "repo/.anaxigraph-v2/state.sqlite").write_bytes(b"saved-state")
    (chain / "repo/.venv").mkdir()
    (chain / "repo/.venv/large-cache").write_text("do not duplicate")
    previous = {
        "scenario_identity": {"id": "fixture", "oracle": "frozen"},
        "model": "fixture-model",
        "effort": "high",
        "agent": "noop",
        "review_steps": [6],
        "review_kinds": ["patterns"],
        "runtime_commit": "old-runtime",
    }
    driver.write_json(source / "run-1.json", previous)
    driver.write_json(chain / "chain.json", previous | {"arm": "bare", "scenario": {}})
    driver.append_jsonl(chain / "steps.jsonl", {"step": 0, "spent": 17})
    config = SimpleNamespace(
        state=tmp_path / "continued",
        continue_from=source,
        additional_ci_repairs=3,
        continuation_audit=None,
        provenance=previous | {"runtime_commit": "new-runtime"},
    )
    return config, source, chain


def test_fork_preserves_source_and_history_and_does_not_copy_environments(fork):
    config, source, chain = fork
    before = continuation.source_records(source)
    continuation.prepare(config, driver)
    assert continuation.source_records(source) == before
    target = config.state / chain.name
    assert (target / "steps.jsonl").read_bytes() == (chain / "steps.jsonl").read_bytes()
    assert not (target / "repo/.venv").exists()
    assert (target / "repo/.anaxigraph-v2/state.sqlite").read_bytes() == b"saved-state"
    assert json.loads((target / "chain.json").read_text())["runtime_commit"] == "new-runtime"
    original = json.loads((target / "continuation-original-chain.json").read_text())
    assert original["runtime_commit"] == "old-runtime"
    assert config.continuation["original_provenance"]["runtime_commit"] == "old-runtime"
    config.additional_ci_repairs = 4
    with pytest.raises(ValueError, match="allowance is frozen"):
        continuation.prepare(config, driver)


@pytest.mark.parametrize("key", ["scenario_identity", "model", "effort", "agent"])
def test_fork_rejects_changed_comparison_inputs_before_writing(fork, key):
    config, source, _ = fork
    config.provenance[key] = "changed"
    with pytest.raises(ValueError, match=key):
        continuation.prepare(config, driver)
    assert not config.state.exists()


def test_fork_refuses_source_destination_and_existing_state(fork):
    config, source, _ = fork
    config.state = source
    with pytest.raises(ValueError, match="separate new state"):
        continuation.prepare(config, driver)
    config.state = source.parent / "occupied"
    config.state.mkdir()
    with pytest.raises(ValueError, match="must not already exist"):
        continuation.prepare(config, driver)


def test_continuation_cannot_be_reported_as_original_comparative_win():
    arms = {name: {"continuation_chains": [name + "-r0"]} for name in ("charter", "anaxigraph")}
    assert report.decide(arms)["verdict"] == "exploratory-continuation"


def test_aggregated_interruption_keeps_unknown_cost_and_known_lower_bound():
    first = {"seconds": None, "sessions": 1, "usage": None, "usage_unknown_sessions": 1}
    second = {
        "seconds": 7,
        "usage": {"input_tokens": 17, "cached_input_tokens": 2, "output_tokens": 3},
    }
    result = driver.aggregate_agents([{"agent": first}, {"agent": second}])
    assert result["seconds"] is None and result["known_seconds"] == 7
    assert result["sessions"] == 2 and result["seconds_unknown_sessions"] == 1
    assert result["usage_unknown_sessions"] == 1
    assert result["usage"]["input_tokens"] is None


def test_completed_streamed_call_is_reused_without_a_second_paid_session(tmp_path, monkeypatch):
    log = tmp_path / "repair-3.jsonl"
    log.write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 17, "cached_input_tokens": 2, "output_tokens": 3},
            }
        )
        + "\n"
    )
    driver.write_json(log.with_suffix(".session.json"), {"status": "completed", "seconds": 7})
    monkeypatch.setattr(
        driver, "run_agent", lambda *args: pytest.fail("Replayed a spent model call")
    )
    recovered = continuation.run_or_recover(None, tmp_path, log, "task", False, driver)
    assert recovered["seconds"] == 7 and recovered["usage"]["input_tokens"] == 17
    assert recovered["recovered_session"]["transcript_sha256"] == continuation.fingerprint(log)


def test_a_live_recorded_coding_process_cannot_be_recovered_by_another_writer(tmp_path):
    log = tmp_path / "step.jsonl"
    driver.write_json(log.with_suffix(".session.json"), {"status": "running", "pid": os.getpid()})
    with pytest.raises(ValueError, match="still running"):
        continuation.recovered_session(log, driver)


def test_dead_interrupted_call_keeps_unknown_usage_and_elapsed_time(tmp_path):
    log = tmp_path / "step.jsonl"
    driver.write_json(log.with_suffix(".session.json"), {"status": "running", "pid": 999999999})
    log.write_text('{"type":"turn.started"}\n')
    agent = continuation.recovered_session(log, driver)
    assert agent["status"] == "interrupted"
    assert agent["seconds"] is None and agent["usage"] is None
    assert agent["usage_unknown_sessions"] == 1


def test_contamination_override_is_bound_to_original_transcript_hash(fork):
    config, source, chain = fork
    log = chain / "public-log.jsonl"
    log.write_text("public command evidence")
    audit = source.parent / "audit.json"
    driver.write_json(
        audit,
        {
            "frozen_state": str(source),
            "scope": "fixture public-documentation read",
            "continuation_override_category": "audited-public-documentation-reference",
            "scenario_hidden_hits": [],
            "matches": [{"path": str(log), "line": 1, "source_sha256": "tampered"}],
        },
    )
    config.continuation_audit = audit
    with pytest.raises(ValueError, match="does not match"):
        continuation.prepare(config, driver)
    value = json.loads(audit.read_text())
    value["matches"][0]["source_sha256"] = continuation.fingerprint(log)
    driver.write_json(audit, value)
    continuation.prepare(config, driver)
    assert config.continuation["audited_contamination"]["original_flag_preserved"]


def test_repair_reentry_retains_original_attempts_and_never_resets_allowance(tmp_path, monkeypatch):
    config = SimpleNamespace(
        ci_repair_attempts=2, agent="fixture", continuation={"additional_ci_repairs": 1}
    )
    prior = [
        {
            "number": n,
            "agent": {
                "seconds": 5,
                "usage": {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3},
            },
            "commit": f"commit-{n}",
            "evaluation": {"hidden": {"failed": []}},
            "gate": {"status": "failed"},
            "feedback": {"gate": {"status": "failed"}},
            "verification_seconds": 3,
            "anaxigraph_seconds": 2,
        }
        for n in range(3)
    ]
    monkeypatch.setattr(driver.collector, "inventory", lambda repo: {"tests/test_a.py": "saved"})
    monkeypatch.setattr(driver.metrics, "collect", lambda *args: ["tests/test_a.py::test_a"])
    calls = []
    monkeypatch.setattr(
        driver,
        "repair_candidate",
        lambda *args: (
            calls.append(args)
            or {
                "seconds": 7,
                "usage": {"input_tokens": 17, "cached_input_tokens": 4, "output_tokens": 5},
            },
            "new-head",
        ),
    )
    monkeypatch.setattr(
        driver,
        "safety_feedback",
        lambda *args, **kwargs: {
            "evaluation": {"ci": {}, "hidden": {"failed": []}},
            "gate": {"status": "passed"},
            "feedback": {"gate": {"status": "passed"}},
            "test_inventory": ["tests/test_a.py::test_a"],
            "verification_seconds": 4,
            "anaxigraph_seconds": 1,
        },
    )
    result = driver.repair_ci(
        config,
        tmp_path,
        tmp_path / "safety",
        "task",
        {},
        5,
        "original-base",
        "commit-2",
        prior_attempts=prior,
        baseline_id="mapped-original",
    )
    assert len(calls) == 1 and calls[0][5] == 3
    assert calls[0][-2] == "mapped-original"
    assert result["ci_attempts"][:3] == prior
    assert len(result["ci_attempts"]) == 4
    assert result["agent"]["seconds"] == 22 and result["agent"]["sessions"] == 4
    assert result["verification_seconds"] == 13 and result["ci_validation_seconds"] == 7
    config.continuation["additional_ci_repairs"] = 3
    again = driver.repair_ci(
        config,
        tmp_path,
        tmp_path / "safety",
        "task",
        {},
        5,
        "original-base",
        "new-head",
        prior_attempts=result["ci_attempts"],
    )
    assert len(calls) == 1 and again["agent"]["seconds"] == 22


def test_fork_and_pending_recovery_preserve_original_failure_and_all_spent_calls(fork, monkeypatch):
    config, source, chain = fork
    attempts = [
        {
            "number": n,
            "agent": {
                "seconds": 5,
                "usage": {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3},
            },
            "commit": f"commit-{n}",
            "evaluation": {"hidden": {"failed": []}},
            "gate": {"status": "failed"},
            "feedback": {"gate": {"status": "failed"}},
            "verification_seconds": 3,
            "anaxigraph_seconds": 0,
        }
        for n in range(3)
    ]
    pending = {
        "step": 5,
        "arm": "bare",
        "replicate": 0,
        "base_commit": "base",
        "commit": "commit-2",
        "baseline_inputs": {},
        "agent": driver.aggregate_agents(attempts),
        "ci_attempts": attempts,
        "ci_gate": {"status": "failed"},
    }
    driver.write_json(chain / "pending-step-05.json", pending)
    (chain / "logs").mkdir()
    (chain / "logs/step-05.prompt.md").write_text("Original task")
    before = continuation.source_records(source)
    continuation.prepare(config, driver)
    config.first_step = config.last_step = 5
    config.ci_repair_attempts, config.agent = 2, "fixture"
    monkeypatch.setattr(driver, "CI_FROM", 5)
    monkeypatch.setattr(driver, "sh", lambda *args, **kwargs: SimpleNamespace(stdout=""))
    monkeypatch.setattr(driver.collector, "inventory", lambda repo: {"tests/test_a.py": "saved"})
    monkeypatch.setattr(driver.metrics, "collect", lambda *args: ["tests/test_a.py::test_a"])
    model_calls = []
    monkeypatch.setattr(
        driver,
        "repair_candidate",
        lambda *args: (
            model_calls.append(args)
            or {
                "seconds": 7,
                "usage": {"input_tokens": 17, "cached_input_tokens": 4, "output_tokens": 5},
            },
            "new-head",
        ),
    )
    monkeypatch.setattr(
        driver,
        "safety_feedback",
        lambda *args, **kwargs: {
            "evaluation": {"ci": {}, "hidden": {"failed": []}},
            "gate": {"status": "passed"},
            "feedback": {"gate": {"status": "passed"}},
            "test_inventory": ["tests/test_a.py::test_a"],
            "verification_seconds": 4,
            "anaxigraph_seconds": 0,
        },
    )
    target = config.state / chain.name
    continuation.resume(config, target, driver)
    assert continuation.source_records(source) == before
    assert json.loads((chain / "pending-step-05.json").read_text()) == pending
    assert not (target / "pending-step-05.json").exists()
    rows = driver.read_jsonl(target / "steps.jsonl")
    assert rows[0] == {"step": 0, "spent": 17}
    assert rows[1]["agent"]["seconds"] == 22
    assert rows[1]["ci_attempts"][:3] == attempts
    assert rows[1]["continuation"]["original_ci_gate"]["status"] == "failed"
    assert len(model_calls) == 1
    driver.write_json(target / "pending-step-05.json", pending)
    continuation.resume(config, target, driver)
    assert len(model_calls) == 1 and len(driver.read_jsonl(target / "steps.jsonl")) == 2
    assert not (target / "pending-step-05.json").exists()


def test_original_baseline_is_rebuilt_at_same_git_tree_and_candidate_restored(
    tmp_path, monkeypatch
):
    repo = tmp_path / "chain/repo"
    repo.mkdir(parents=True)
    driver.sh(["git", "init", "-q", "-b", "main"], cwd=repo)
    driver.sh(["git", "config", "user.name", "fixture"], cwd=repo)
    driver.sh(["git", "config", "user.email", "fixture@example.invalid"], cwd=repo)
    (repo / "a.py").write_text("value = 1\n")
    base = driver.commit(repo, "Original baseline")
    original_inputs = driver.collector.inventory(repo)
    (repo / "a.py").write_text("value = 2\n")
    head = driver.commit(repo, "Candidate")
    seen = []

    def runtime(config, root, operation, *args):
        seen.append((operation, (root / "a.py").read_text()))
        return (
            {"snapshot": {"id": "fork-baseline"}}
            if operation == "scan"
            else {"baseline_id": "fork-baseline"}
        )

    monkeypatch.setattr(driver, "runtime", runtime)
    row = {
        "step": 5,
        "base_commit": base,
        "commit": head,
        "baseline_inputs": original_inputs,
        "baseline_id": "old-path-id",
    }
    continuation.original_baseline(SimpleNamespace(), repo.parent, row, driver)
    assert row["baseline_id"] == "fork-baseline"
    assert seen == [("scan", "value = 1\n"), ("prepare", "value = 1\n")]
    assert driver.sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip() == head
    assert (repo / "a.py").read_text() == "value = 2\n"
    mapped = json.loads((repo.parent / "continuation-baseline-05.json").read_text())
    assert mapped["original_baseline_id"] == "old-path-id"
    assert mapped["base_commit"] == base
    driver.sh(["git", "checkout", "--detach", base], cwd=repo)
    continuation.original_baseline(SimpleNamespace(), repo.parent, row, driver)
    assert driver.sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip() == head
    assert (repo / "a.py").read_text() == "value = 2\n"


def test_interrupted_live_model_is_checked_before_any_git_mutation(tmp_path, monkeypatch):
    chain = tmp_path / "bare-r0"
    (chain / "logs").mkdir(parents=True)
    driver.write_json(
        chain / "logs/step-05.session.json", {"status": "running", "pid": os.getpid()}
    )
    monkeypatch.setattr(
        driver, "commit", lambda *args: pytest.fail("Committed during active coding")
    )
    monkeypatch.setattr(
        driver, "sh", lambda *args, **kwargs: pytest.fail("Changed Git during active coding")
    )
    with pytest.raises(ValueError, match="still running"):
        continuation.interrupted_row(SimpleNamespace(), chain, 5, driver)


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_completed_paid_candidate_recovers_only_an_exact_unrestored_evaluator_fault(
    tmp_path, ending
):
    chain = tmp_path / "bare-r0"
    repo = chain / "repo"
    repo.mkdir(parents=True)
    driver.sh(["git", "init", "-q", "-b", "main"], cwd=repo)
    driver.sh(["git", "config", "user.name", "fixture"], cwd=repo)
    driver.sh(["git", "config", "user.email", "fixture@example.invalid"], cwd=repo)
    original = (
        "def choose(value):\n    if value > 2:\n        return value + 1\n    return 0\n".replace(
            "\n", ending
        )
    )
    file = repo / "logic.py"
    file.write_bytes(original.encode())
    head = driver.commit(repo, "Step 17: completed paid candidate")
    (chain / "logs").mkdir()
    (chain / "logs/step-17.jsonl").write_text('{"type":"turn.completed"}\n')
    mutant = next(
        driver.metrics.mutate(original, index)[0]
        for index, _ in driver.metrics.candidate_mutants(original)
        if driver.metrics.mutate(original, index)[1][0] == "negate_if"
    )
    file.write_text(mutant)
    proof = continuation.recover_legacy_mutant(chain, 17, {"status": "recovered-completed"}, driver)
    assert proof["status"] == "restored-paid-candidate"
    assert file.read_bytes() == original.encode()
    assert (chain / "legacy-mutant-step-17.bin").read_text() == mutant
    assert driver.sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip() == head
    file.write_text(original + "\ndef independent_work():\n    return True\n")
    with pytest.raises(ValueError, match="not one exact"):
        continuation.recover_legacy_mutant(chain, 17, {"status": "recovered-completed"}, driver)
    assert "independent_work" in file.read_text()
