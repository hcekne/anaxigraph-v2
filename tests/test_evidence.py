"""Actual bounded trial files and provider metadata, without paid model calls."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from anaxigraph.evidence import Evidence, provider_usage


def test_disabled_and_unstarted_evidence_do_not_create_files(tmp_path):
    evidence = Evidence(tmp_path)
    assert evidence.summary()["events"] == 0
    assert evidence.events()["items"] == []
    assert not evidence.path.exists()
    disabled = Evidence(tmp_path, enabled=False)
    assert disabled.start_task("Add export", "abc", "snapshot", "baseline") is None
    assert disabled.record("context_supplied", {"entity_ids": ["a"]}) is None
    assert disabled.export()["events"] == []
    assert not disabled.path.exists()


def test_task_context_outcome_and_actual_consultation_survive_restart(tmp_path):
    evidence = Evidence(tmp_path, arm="treatment", experiment_id="snow-rankings-1")
    identity = evidence.start_task("Add invoice export", "abc", "before", "baseline")
    evidence.record("context_supplied", {"snapshot_id": "before", "entity_ids": ["one", "two"]})
    evidence.consulted("one", "before")
    evidence.outcome(
        "reported-passed",
        "after",
        [{"check": "pytest", "status": "reported-passed"}],
        "A fresh agent still needs to attempt the next task.",
    )
    resumed = Evidence(tmp_path, arm="treatment", experiment_id="snow-rankings-1")
    assert resumed.start_task("Add  invoice export", "abc", "after", "baseline") == identity
    events = resumed.events(task_id=identity)
    assert [item["kind"] for item in events["items"]] == [
        "task_started",
        "context_supplied",
        "context_consulted",
        "task_outcome",
    ]
    assert events["items"][0]["payload"]["starting_revision"] == "abc"
    assert events["items"][0]["payload"]["baseline_id"] == "baseline"
    assert events["items"][2]["payload"]["entity_id"] == "one"
    assert all(item["payload"]["arm"] == "treatment" for item in events["items"])
    assert resumed.start_task("Add export filters", "abc", "after", "after") != identity
    assert resumed.start_task(
        "Rephrase task while continuing", "new", "later", "later", continue_task=True
    )


def test_missing_usage_is_unknown_not_a_zero_cost_call(tmp_path):
    evidence = Evidence(tmp_path)
    evidence.start_task("Build ranker", "commit", "snapshot", "baseline")
    evidence.model_call("codex", "model", "mapping", "failed", 2.1, attempt=1)
    item = evidence.events()["items"][-1]["payload"]
    assert item["cost_usd"] is None and item["input_tokens"] is None
    assert item["usage_source"] == item["cost_source"] == "unknown"
    summary = evidence.summary()
    assert summary["unknown_cost_calls"] == 1
    assert summary["event_kinds"]["model_call"] == 1


def test_bounded_retention_is_disclosed_and_pages_and_exports_are_real(tmp_path):
    evidence = Evidence(tmp_path, max_events=3)
    for i in range(8):
        evidence.record("workflow", {"run_id": f"run-{i}", "status": "completed"})
    assert evidence.summary()["events"] == 3
    assert evidence.summary()["dropped_events"] == 5
    assert evidence.summary()["complete"] is False
    first = evidence.events(2)
    assert first["next_offset"] == 2
    assert evidence.events(2, first["next_offset"])["items"][0]["payload"]["run_id"] == "run-7"
    path = tmp_path / "trial-result.json"
    exported = evidence.export(path)
    assert json.loads(path.read_text()) == exported
    assert len(exported["events"]) == 3
    assert not list(tmp_path.glob(".trial-*"))
    assert evidence.path.stat().st_size <= evidence.max_bytes


def test_real_sqlite_capacity_error_preserves_prior_events_and_budget(tmp_path):
    evidence = Evidence(tmp_path, max_bytes=65_536, max_events=100)
    evidence.record("workflow", {"status": "initial"})
    previous = None
    for i in range(100):
        previous = evidence.events()["total"]
        try:
            evidence.record("workflow", {"notes": "x" * 4000, "run_id": str(i)})
        except ValueError:
            break
    else:
        pytest.fail("Fixture did not exercise the actual SQLite capacity limit")
    assert evidence.events()["total"] == previous
    assert evidence.events()["items"][0]["payload"]["status"] == "initial"
    assert evidence.path.stat().st_size <= 65_536


def test_concurrent_workers_save_every_event_without_losing_task_attribution(tmp_path):
    evidence = Evidence(tmp_path)
    task_id = evidence.start_task("Build ranking", "abc", "before", "baseline")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda i: evidence.model_call(
                    "codex", "model", "mapping", "completed", 0.1, request_id=f"job-{i}"
                ),
                range(30),
            )
        )
    events = evidence.events(100)
    assert events["total"] == 31
    assert all(item["task_id"] == task_id for item in events["items"])


def test_concurrent_same_task_preparation_starts_only_one_trial_task(tmp_path):
    evidence = Evidence(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        identities = list(
            pool.map(
                lambda _i: evidence.start_task("Build ranking", "abc", "before", "baseline"),
                range(20),
            )
        )
    assert len(set(identities)) == 1
    assert evidence.summary()["event_kinds"] == {"task_started": 1}


def test_source_credentials_and_oversize_payloads_are_not_accepted(tmp_path):
    evidence = Evidence(tmp_path)
    for value in (
        {"raw_source": "sensitive"},
        {"metrics": {"api_key": "sensitive"}},
        {"notes": "x" * 4001},
        {"entity_ids": list(range(101))},
    ):
        with pytest.raises(ValueError):
            evidence.record("workflow", value)
    assert not evidence.path.exists()
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".anaxigraph-v2").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        evidence.record("workflow", {"status": "completed"})


def test_codex_completed_events_sum_turn_usage_without_guessing_dollars():
    # The installed CLI's --json format is documented by its own official guide.
    result = provider_usage(
        "codex",
        "\n".join(
            [
                '{"type":"thread.started","thread_id":"opaque"}',
                '{"type":"item.completed","item":{"text":"do not persist model text"}}',
                '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":80,"output_tokens":20,"reasoning_output_tokens":5}}',
                '{"type":"turn.completed","usage":{"input_tokens":200,"cached_input_tokens":180,"output_tokens":30,"reasoning_output_tokens":10}}',
            ]
        ),
    )
    assert result["input_tokens"] == 300
    assert result["cached_input_tokens"] == 260
    assert result["output_tokens"] == 50 and result["total_tokens"] == 350
    assert result["reasoning_output_tokens"] == 15
    assert result["cost_usd"] is None and result["cost_source"] == "unknown"
    assert result["usage_source"] == "codex-turn.completed"
    assert "do not persist" not in json.dumps(result)


def test_claude_cache_input_is_added_and_cost_remains_a_provider_estimate():
    result = provider_usage(
        "claude",
        {
            "type": "result",
            "result": "model text ignored",
            "total_cost_usd": 0.123,
            "usage": {
                "input_tokens": 20,
                "cache_read_input_tokens": 80,
                "cache_creation_input_tokens": 10,
                "output_tokens": 30,
            },
        },
    )
    assert result["input_tokens"] == 110
    assert result["total_tokens"] == 140
    assert result["cached_input_tokens"] == 80 and result["cache_write_input_tokens"] == 10
    assert result["cost_usd"] == 0.123 and result["cost_source"] == "provider-reported-estimate"


def test_invalid_or_partial_usage_is_not_filled_with_zeroes():
    result = provider_usage(
        "claude",
        {
            "type": "result",
            "usage": {"input_tokens": 20, "output_tokens": False},
            "total_cost_usd": float("nan"),
        },
    )
    assert result["uncached_input_tokens"] == 20
    assert result["input_tokens"] is None
    assert result["output_tokens"] is None and result["cost_usd"] is None
    assert provider_usage("codex", "not JSON")["usage_source"] == "unknown"
    with pytest.raises(ValueError):
        provider_usage("unknown-provider", {})
