"""Exercise the replay's real persistence and equal-budget measurement contracts."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay  # noqa: E402
import scoring  # noqa: E402


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def test_actual_replay_resumes_without_duplicate_rows_and_rejects_changed_inputs(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    for value in range(3):
        (source / "invoice.py").write_text(f"def invoice():\n    return {value}\n")
        git(source, "add", ".")
        git(
            source,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "change invoice",
        )
    args = argparse.Namespace(
        source=source,
        workdir=tmp_path / "run",
        mode="structural",
        config=None,
        commits=2,
        max_files=8,
        tasks=None,
        candidate_budget=3,
    )
    replay.run(args)
    result = args.workdir / "results-structural.jsonl"
    first = result.read_bytes()
    replay.run(args)
    assert result.read_bytes() == first
    rows = [json.loads(line) for line in first.splitlines()]
    assert len(rows) == len({row["commit"] for row in rows}) == 2
    assert all(len(row["commit"]) == 40 for row in rows)
    args.candidate_budget = 4
    with pytest.raises(ValueError, match="inputs/runtime changed"):
        replay.run(args)


def test_unverified_warm_state_and_abbreviated_or_duplicate_rows_are_rejected(tmp_path):
    results = tmp_path / "results-structural.jsonl"
    results.write_text(json.dumps({"commit": "a" * 12}) + "\n")
    with pytest.raises(ValueError, match="abbreviated"):
        replay.completed_results(results)
    results.write_text((json.dumps({"commit": "a" * 40}) + "\n") * 2)
    with pytest.raises(ValueError, match="Duplicate"):
        replay.completed_results(results)
    args = argparse.Namespace(mode="mapped", candidate_budget=6)
    with pytest.raises(ValueError, match="Unverified warm"):
        replay._manifest(tmp_path, tmp_path, args, [], None)


def test_budget_metrics_keep_no_predictions_visible_and_do_not_conflate_recall_with_precision():
    assert scoring.precision(set(), {"a.py"}) == 0
    assert scoring.precision({"a.py", "b.py"}, {"a.py"}) == 0.5
    assert scoring.precision({"a.py"}, set()) is None
    assert scoring.reciprocal_fusion([["a.py", "b.py"], ["c.py", "b.py"]], 1) == ["b.py"]
    summary = scoring.summarize(
        [
            {"oracle_consumer_recall_anaxigraph": 1, "oracle_consumer_size_anaxigraph": 2},
            {"oracle_consumer_recall_anaxigraph": None, "oracle_consumer_size_anaxigraph": 50},
        ]
    )
    assert summary["eligible_list_sizes"]["consumer_anaxigraph"] == {"mean": 2, "n": 1}
