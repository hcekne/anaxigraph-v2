"""Campaign planning must not spend model calls or silently change frozen inputs."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from . import campaign, run


def test_frozen_campaign_uses_existing_runner_and_counts_opportunities(tmp_path):
    root = tmp_path / "source"
    runtime = root / ".venv/bin/anaxigraph"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("entrypoint")
    with patch.object(campaign, "source_identity", return_value={"git_head": "pinned"}):
        plan = campaign.build(root, tmp_path / "private", replicates=3)
    assert plan["status"] == "planned-not-run" and plan["planned_coding_turns"] == 72
    assert plan["analysis_call_count"] is None and plan["cost_usd"] is None
    assert len(plan["comparisons"]) == 3
    for row in plan["comparisons"]:
        args = run.parser().parse_args(row["command"]["argv"][3:])
        assert args.live and args.track == "discovery" and args.replicates == 3
        assert args.stages == 4 and args.runtime_source_root == root / "src"
    assert plan["comparisons"][2]["command"]["argv"].count("review") == 1
    for row in plan["qualification"]:
        args = run.parser().parse_args(row["argv"][3:])
        assert args.synthetic and not args.live
    with pytest.raises(ValueError, match="outside"):
        campaign.build(root, root / "private")


def test_plan_cli_writes_real_identity_once_without_launching_clients(tmp_path, capsys):
    output = tmp_path / "plan.json"
    args = ["--state-root", str(tmp_path / "private"), "--output", str(output)]
    with patch.object(run, "agent_call", side_effect=AssertionError("Planning calls no models")):
        campaign.main(args)
    plan = json.loads(output.read_text())
    assert plan["runtime_source"]["git_head"]
    assert "experiments/architecture_quality/campaign.py" in plan["runtime_source"]["files"]
    assert not any(path.startswith(".venv/") for path in plan["runtime_source"]["files"])
    assert json.loads(capsys.readouterr().out)["model_calls"] == 0
    with pytest.raises(FileExistsError):
        campaign.main(args)


def test_blind_task_export_hides_coding_effort_but_keeps_private_outcomes():
    rows = [
        {
            "task": "feature",
            "patch": {"changed_paths": ["solution.py"]},
            "outcome": {"qualified": True},
            "coding": {"elapsed_seconds": 500, "tokens": {"input_tokens": 100000}},
        }
    ]
    assert run.blind_tasks(rows) == [
        {"task": rows[0]["task"], "patch": rows[0]["patch"], "outcome": rows[0]["outcome"]}
    ]
    assert rows[0]["coding"]["elapsed_seconds"] == 500


def test_failed_candidate_check_paths_cannot_disclose_assignment(tmp_path):
    from . import coupled

    root = tmp_path / "treatment"
    coupled.seed(root)
    check = root / "tests/test_smoke.py"
    check.write_text(
        check.read_text().replace("state = {}", "self.fail('probe')\n        state = {}")
    )
    outcome = coupled.evaluate(root, random_seed=42)
    assert str(root) in json.dumps(outcome)
    blinded = run.blind_evidence(outcome, root)
    assert str(root) not in json.dumps(blinded)
    assert "/candidate/tests/test_smoke.py" in json.dumps(blinded)
    assert str(root) in json.dumps(outcome), "Private failure logs must retain their exact path"


def test_frozen_campaign_refuses_changed_source_or_unplanned_configuration(tmp_path):
    output = tmp_path / "plan.json"
    campaign.main(["--state-root", str(tmp_path / "private"), "--output", str(output)])
    plan = json.loads(output.read_text())
    config = run.parser().parse_args(plan["pilot"]["argv"][3:])
    campaign.verify(config)
    config.replicates += 1
    with pytest.raises(ValueError, match="Invocation differs"):
        campaign.verify(config)
    config.replicates -= 1
    with patch.object(campaign, "source_identity", return_value={"files_sha256": "changed"}):
        with pytest.raises(ValueError, match="source/runtime changed"):
            campaign.verify(config)
    assert not config.state.exists(), "Verification must fail before trial state is written"
