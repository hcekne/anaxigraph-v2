"""Judgments cannot become arm-labelled evidence until completed and frozen."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from . import review, run

LABEL_A = "candidate-5d998017f5e2fc57"
LABEL_B = "candidate-2cb85f3f4a24e39a"


def fixture_row(root):
    before = {"solution.py": "def increment(count):\n    return count + 0\n"}
    return {
        "stage": 0,
        "task": "Add increment",
        "baseline_digest": review.digest(before),
        "patch": run.delta(before, run.snapshot(root)),
        "outcome": {"behavior_passed": True, "qualified": True, "ci_safe": True},
        "coding": {
            "tokens": {"input_tokens": 123},
            "elapsed_seconds": None,
            "cost_usd": None,
            "control_isolation": {"passed": True},
        },
        "advice_consulted": False,
    }


@pytest.fixture
def state(tmp_path):
    trial = tmp_path / "trial"
    for label, arm in ((LABEL_A, "control"), (LABEL_B, "treatment")):
        candidate = trial / "blind-review" / label
        candidate.mkdir(parents=True)
        (candidate / "solution.py").write_text("def increment(count):\n    return count + 1\n")
        root = trial / "fixture-0" / arm
        root.mkdir(parents=True)
        (root / "solution.py").write_bytes((candidate / "solution.py").read_bytes())
        row = fixture_row(root)
        evidence = root.parent / f"{arm}-evidence" / "task-0"
        evidence.mkdir(parents=True)
        before = {"solution.py": "def increment(count):\n    return count + 0\n"}
        review.save_new(
            evidence / "baseline.private.json",
            {
                "files": before,
                "digest": review.digest(before),
            },
        )
        review.save_new(evidence / "result.private.json", row)
        review.save_new(candidate / "tasks.json", run.blind_tasks([row], root))
        review.save_new(candidate / "outcomes.json", [row["outcome"]])
        (candidate / "diffs").mkdir()
        (candidate / "diffs/task-0.patch").write_text(run.blind_diff(root, arm, 0))
    return trial


def completed_sheet(state, tmp_path):
    output = tmp_path / "sheet"
    sheet = review.prepare(state / "blind-review", output)
    sheet["reviewer"] = {
        "id": "reviewer-1",
        "kind": "human",
        "model": None,
        "reasoning_effort": None,
    }
    for row in sheet["reviews"]:
        for dimension in row["dimensions"].values():
            dimension.update(
                score=2,
                evidence=[
                    {
                        "path": "solution.py",
                        "quote": "return count + 1",
                        "reason": "The operation is directly expressed behind a small public function.",
                    }
                ],
            )
        row["uncertainty"] = {"level": "high", "reason": "This is only a tiny test fixture."}
        row["suspected_unblinding"] = {
            "suspected": False,
            "explanation": "No process provenance found.",
            "evidence": [],
        }
        row["summary"] = (
            "Direct implementation; this fixture cannot establish later-change quality."
        )
    (output / "sheet.json").write_text(json.dumps(sheet))
    return output / "sheet.json", sheet


def private_measurements(state):
    review.save_new(
        state / "blind-map.private.json",
        {
            LABEL_A: {"scenario": "fixture", "replicate": 0, "arm": "control"},
            LABEL_B: {"scenario": "fixture", "replicate": 0, "arm": "treatment"},
        },
    )
    manifest = {
        "evidence_kind": "synthetic",
        "status": "completed",
        "control_mode": "normal",
        "track": "guided",
        "pairs": [
            {
                "scenario": "fixture",
                "replicate": 0,
                "hidden_sample_seed": 2241903809,
                "arms": {
                    arm: [fixture_row(state / "fixture-0" / arm)]
                    for arm in ("control", "treatment")
                },
                "analysis_model_receipts": {"control": [], "treatment": []},
            }
        ],
    }
    review.save_new(state / "invocation.private.json", {"random_seed": 17})
    review.save_new(state / "run.private.json", manifest)
    review.save_new(state / "comparison.json", run.comparison(manifest))


def test_prepare_is_anonymous_and_ungraded_without_reading_private_files(state, tmp_path):
    sheet = review.prepare(state / "blind-review", tmp_path / "grading")
    assert not (state / "blind-map.private.json").exists()
    assert sheet["reviewer"]["kind"] is None
    assert all(
        value["score"] is None for row in sheet["reviews"] for value in row["dimensions"].values()
    )
    assert "control" not in json.dumps(sheet)
    assert (tmp_path / "grading/schema.json").is_file()
    assert (tmp_path / "grading/rubric.md").read_bytes() == review.RUBRIC.read_bytes()
    with pytest.raises(ValueError):
        review.validate(sheet, state / "blind-review")
    with pytest.raises(FileExistsError):
        review.prepare(state / "blind-review", tmp_path / "grading")


@pytest.mark.parametrize(
    "alter",
    [
        lambda sheet: sheet["reviews"][0]["dimensions"]["simplicity"].update(score=True),
        lambda sheet: sheet["reviews"][0]["dimensions"]["simplicity"].update(score=4),
        lambda sheet: sheet["reviews"][0]["dimensions"]["simplicity"].update(evidence=[]),
        lambda sheet: sheet["reviews"][0]["dimensions"]["simplicity"]["evidence"][0].update(
            quote="an invented source claim"
        ),
        lambda sheet: sheet["reviews"][0]["dimensions"]["simplicity"]["evidence"][0].update(
            path="../../blind-map.private.json"
        ),
        lambda sheet: sheet["reviews"][0]["uncertainty"].update(reason=" "),
        lambda sheet: sheet["reviews"][0]["suspected_unblinding"].update(suspected=True),
        lambda sheet: sheet["reviews"][0].pop("summary"),
        lambda sheet: sheet["reviews"][1].update(candidate=sheet["reviews"][0]["candidate"]),
        lambda sheet: sheet["reviewer"].update(kind="model"),
        lambda sheet: sheet["reviewer"].update(model="invented-human-model"),
    ],
)
def test_incomplete_or_unsupported_judgments_cannot_freeze(state, tmp_path, alter):
    path, sheet = completed_sheet(state, tmp_path)
    alter(sheet)
    path.write_text(json.dumps(sheet))
    with pytest.raises(ValueError):
        review.freeze(state / "blind-review", path, tmp_path / "frozen.json")
    assert not (tmp_path / "frozen.json").exists()


def test_model_and_unblinding_provenance_are_explicit(state, tmp_path):
    path, sheet = completed_sheet(state, tmp_path)
    sheet["reviewer"].update(kind="model", model="recorded-test-model", reasoning_effort="high")
    row = sheet["reviews"][0]
    row["suspected_unblinding"] = {
        "suspected": True,
        "explanation": "A quoted fragment may identify the source.",
        "evidence": copy.deepcopy(row["dimensions"]["simplicity"]["evidence"]),
    }
    path.write_text(json.dumps(sheet))
    frozen = review.freeze(state / "blind-review", path, tmp_path / "frozen.json")
    assert frozen["judgment"]["reviewer"]["kind"] == "model"
    assert frozen["judgment"]["reviews"][0]["suspected_unblinding"]["suspected"] is True


def test_changed_bundles_are_rejected_before_freeze(state, tmp_path):
    path, _ = completed_sheet(state, tmp_path)
    (state / "blind-review/candidate-5d998017f5e2fc57/solution.py").write_text("changed source")
    with pytest.raises(ValueError, match="frozen value"):
        review.freeze(state / "blind-review", path, tmp_path / "frozen.json")


def test_symlink_evidence_is_rejected(state, tmp_path):
    (state / "blind-review/candidate-5d998017f5e2fc57/private").symlink_to(
        tmp_path, target_is_directory=True
    )
    with pytest.raises(ValueError, match="symlinks"):
        review.prepare(state / "blind-review", tmp_path / "grading")


def test_freeze_refuses_overwrite_and_join_checks_before_opening_assignments(state, tmp_path):
    path, _ = completed_sheet(state, tmp_path)
    frozen_path = tmp_path / "frozen.json"
    frozen = review.freeze(state / "blind-review", path, frozen_path)
    with pytest.raises(FileExistsError):
        review.freeze(state / "blind-review", path, frozen_path)
    frozen["judgment"]["reviews"][0]["summary"] = "Altered after freezing"
    frozen_path.write_text(json.dumps(frozen))
    # No private assignment file exists: rejection must precede any attempt to read it.
    with pytest.raises(ValueError, match="Frozen judgment changed"):
        review.join(state, [frozen_path], tmp_path / "joined.json")


def test_join_preserves_separate_outcomes_and_unknown_costs(state, tmp_path):
    path, _ = completed_sheet(state, tmp_path)
    frozen_path = tmp_path / "frozen.json"
    review.freeze(state / "blind-review", path, frozen_path)
    private_measurements(state)
    report = review.join(state, [frozen_path], tmp_path / "joined.json")
    candidate = report["candidates"][0]
    assert report["evidence_kind"] == "synthetic"
    assert candidate["architecture"][0]["reviewer"]["kind"] == "human"
    assert candidate["correctness"][0]["behavior_passed"] is True
    assert candidate["effort"]["coding"]["cost_usd"]["reported_sum"] is None
    assert "score" not in candidate
    assert "preference" not in candidate
    with pytest.raises(FileExistsError):
        review.join(state, [frozen_path], tmp_path / "joined.json")
    with pytest.raises(ValueError, match="unique"):
        review.join(state, [frozen_path, frozen_path], tmp_path / "duplicate.json")


def test_frozen_source_hashes_are_verified_again_before_join(state, tmp_path):
    path, _ = completed_sheet(state, tmp_path)
    frozen_path = tmp_path / "frozen.json"
    review.freeze(state / "blind-review", path, frozen_path)
    (state / "blind-review/candidate-2cb85f3f4a24e39a/tasks.json").write_text("[]")
    with pytest.raises(ValueError, match="frozen value"):
        review.join(state, [frozen_path], tmp_path / "joined.json")


@pytest.mark.parametrize("swap_both", [False, True])
def test_duplicate_and_swapped_assignments_are_rejected(state, tmp_path, swap_both):
    path, _ = completed_sheet(state, tmp_path)
    frozen_path = tmp_path / "frozen.json"
    review.freeze(state / "blind-review", path, frozen_path)
    private_measurements(state)
    assignments = review.read(state / "blind-map.private.json")
    assignments[LABEL_B]["arm"] = "control"
    if swap_both:
        assignments[LABEL_A]["arm"] = "treatment"
    (state / "blind-map.private.json").write_text(json.dumps(assignments))
    with pytest.raises(ValueError, match="bijection|randomization"):
        review.join(state, [frozen_path], tmp_path / "joined.json")


@pytest.mark.parametrize("changed", ["source", "outcome", "patch", "comparison"])
def test_join_binds_blind_evidence_to_private_trial(state, tmp_path, changed):
    path, _ = completed_sheet(state, tmp_path)
    frozen_path = tmp_path / "frozen.json"
    review.freeze(state / "blind-review", path, frozen_path)
    private_measurements(state)
    if changed == "source":
        (state / "fixture-0/control/solution.py").write_text("different private candidate")
    elif changed == "patch":
        (state / "fixture-0/control-evidence/task-0/baseline.private.json").write_text(
            json.dumps({"digest": "invalid", "files": {}})
        )
    elif changed == "comparison":
        comparison = review.read(state / "comparison.json")
        comparison["pairs"][0]["arms"]["control"]["coding"]["cost_usd"]["reported_sum"] = 0
        (state / "comparison.json").write_text(json.dumps(comparison))
    else:
        manifest = review.read(state / "run.private.json")
        manifest["pairs"][0]["arms"]["control"][0]["outcome"]["ci_safe"] = False
        (state / "run.private.json").write_text(json.dumps(manifest))
        (state / "comparison.json").write_text(json.dumps(run.comparison(manifest)))
    with pytest.raises(ValueError, match="differs|differ"):
        review.join(state, [frozen_path], tmp_path / "joined.json")


def test_cli_writes_complete_prepare_freeze_join_flow(state, tmp_path):
    directory = tmp_path / "cli-sheet"
    root = Path(__file__).resolve().parents[2]

    def command(*arguments):
        result = subprocess.run(
            [sys.executable, "-m", "experiments.architecture_quality.review", *map(str, arguments)],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    command("prepare", "--blind-root", state / "blind-review", "--output", directory)
    _, sheet = completed_sheet(state, tmp_path)
    (directory / "sheet.json").write_text(json.dumps(sheet))
    frozen = tmp_path / "cli-frozen.json"
    command(
        "freeze",
        "--blind-root",
        state / "blind-review",
        "--sheet",
        directory / "sheet.json",
        "--output",
        frozen,
    )
    private_measurements(state)
    joined = tmp_path / "cli-joined.json"
    command("join", "--state", state, "--frozen", frozen, "--output", joined)
    assert review.read(joined)["trial_status"] == "completed"
    assert len(review.read(joined)["candidates"]) == 2
