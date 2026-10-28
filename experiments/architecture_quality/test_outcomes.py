"""Synthetic qualification proves evaluator sensitivity, never advice effectiveness."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from . import run
from .evaluate import evaluate
from .scenarios import SCENARIOS, seed


@pytest.mark.parametrize("name", SCENARIOS)
def test_equivalent_modular_and_coupled_seeds_are_distinguished(tmp_path, name):
    outcomes = {}
    for modular in (False, True):
        root = tmp_path / str(modular)
        seed(root, name, modular=modular)
        outcomes[modular] = evaluate(root, name, 0, random_seed=42)
    assert all(row["behavior_passed"] and row["ci_safe"] for row in outcomes.values())
    assert outcomes[True]["qualified"]
    assert outcomes[True]["independent_components"] == 2
    assert not outcomes[False]["qualified"]
    assert outcomes[False]["independent_components"] == 1
    # This comparison cannot be won merely by increasing the number of files/packages.
    assert len(list((tmp_path / "True").rglob("*.py"))) == len(
        list((tmp_path / "False").rglob("*.py"))
    )
    safe_scopes = {
        modular: next(
            row["selected"] for row in result["ci_cases"] if row["changed_paths"] == ["adapter.py"]
        )
        for modular, result in outcomes.items()
    }
    assert safe_scopes[True] == ["delivery", "integration"]
    assert safe_scopes[False] == ["compute", "delivery", "integration"]


def test_passing_candidate_tests_cannot_hide_wrong_business_behavior(tmp_path):
    seed(tmp_path, "checkout", modular=True)
    path = tmp_path / "compute.py"
    path.write_text(path.read_text().replace("return value * multiplier", "return 0"))
    outcome = evaluate(tmp_path, "checkout", 0, random_seed=218)
    assert all(row["passed"] for row in outcome["candidate_checks"].values())
    assert not outcome["behavior_passed"]
    assert not outcome["qualified"]


@pytest.mark.parametrize(
    "danger", ["settings.json", "schema.json", "pyproject.toml", "unowned/new.data"]
)
def test_ci_under_testing_shared_or_unknown_inputs_is_rejected(tmp_path, danger):
    seed(tmp_path, "dispatch", modular=True)
    path = tmp_path / "ci_select.py"
    path.write_text("import json\nprint(json.dumps(['delivery', 'integration']))\n")
    outcome = evaluate(tmp_path, "dispatch", 0, random_seed=12, changed_paths=[danger])
    assert outcome["behavior_passed"]
    assert not outcome["ci_safe"]
    assert not outcome["qualified"]
    assert any(not row["safe"] and row["changed_paths"] == [danger] for row in outcome["ci_cases"])


def test_same_signature_behavior_contract_change_still_widens_ci(tmp_path):
    seed(tmp_path, "checkout", modular=True)
    outcome = evaluate(tmp_path, "checkout", 0, random_seed=22)
    contract = next(
        row for row in outcome["ci_cases"] if row["case"] == "compute-semantic-contract"
    )
    assert contract["changed_paths"] == []
    assert contract["required"] == ["compute", "delivery", "integration"]
    assert contract["safe"]


def test_undeclared_transitive_input_cannot_earn_narrow_scope(tmp_path):
    seed(tmp_path, "reporting", modular=False)
    path = tmp_path / "components.json"
    manifest = json.loads(path.read_text())
    manifest["inputs"]["compute"] = ["compute.py"]
    path.write_text(json.dumps(manifest))
    outcome = evaluate(tmp_path, "reporting", 0, random_seed=11)
    assert "compute has undeclared transitive source inputs" in outcome["boundary_violations"]
    assert not outcome["ci_safe"]


def test_ordinary_colocated_functions_are_valid_components(tmp_path):
    seed(tmp_path, "checkout", modular=True)
    combined = (tmp_path / "compute.py").read_text() + "\n" + (tmp_path / "adapter.py").read_text()
    (tmp_path / "units.py").write_text(combined)
    (tmp_path / "compute.py").unlink()
    (tmp_path / "adapter.py").unlink()
    solution = tmp_path / "solution.py"
    solution.write_text(
        solution.read_text()
        .replace("from compute import compute", "from units import compute")
        .replace("from adapter import deliver", "from units import deliver")
    )
    for path in (tmp_path / "tests").glob("*.py"):
        path.write_text(
            path.read_text()
            .replace("from compute import compute", "from units import compute")
            .replace("from adapter import deliver", "from units import deliver")
        )
    run.write(
        tmp_path / "components.json",
        {
            "components": {"compute": "units:compute", "delivery": "units:deliver"},
            "inputs": {"compute": ["units.py"], "delivery": ["units.py"]},
        },
    )
    outcome = evaluate(tmp_path, "checkout", 0, random_seed=27)
    assert outcome["qualified"]
    assert all(
        len(row["selected"]) == 3 for row in outcome["ci_cases"] if row["case"] == "implementation"
    )


@pytest.mark.parametrize("reexport_neighbors", [False, True])
def test_package_initializers_and_relative_imports_are_real_dependencies(
    tmp_path, reexport_neighbors
):
    seed(tmp_path, "checkout", modular=True)
    package = tmp_path / "units"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from .calculation import compute\nfrom .provider import deliver\n"
        if reexport_neighbors
        else ""
    )
    for old, new in (("compute.py", "calculation.py"), ("adapter.py", "provider.py")):
        (package / new).write_text((tmp_path / old).read_text())
        (tmp_path / old).unlink()
    for path in [tmp_path / "solution.py", *(tmp_path / "tests").glob("*.py")]:
        path.write_text(
            path.read_text()
            .replace("from compute import", "from units.calculation import")
            .replace("from adapter import", "from units.provider import")
        )
    run.write(
        tmp_path / "components.json",
        {
            "components": {
                "compute": "units.calculation:compute",
                "delivery": "units.provider:deliver",
            },
            "inputs": {
                "compute": ["units/calculation.py", "units/__init__.py"],
                "delivery": ["units/provider.py", "units/__init__.py"],
            },
        },
    )
    outcome = evaluate(tmp_path, "checkout", 0, random_seed=9)
    assert outcome["behavior_passed"]
    if reexport_neighbors:
        assert not outcome["qualified"]
        assert outcome["independent_components"] == 0
        assert "units/provider.py" in outcome["hidden"]["structure"]["reachable"]["compute"]
    else:
        assert outcome["qualified"]
        assert "units/__init__.py" in outcome["hidden"]["structure"]["reachable"]["compute"]


def test_checks_cannot_rewrite_the_code_after_hidden_behavior_passes(tmp_path):
    seed(tmp_path, "checkout", modular=True)
    path = tmp_path / "tests/test_compute.py"
    path.write_text(
        path.read_text().replace(
            "        self.assertIsInstance",
            "        from pathlib import Path\n        source = Path('compute.py')\n        source.write_text(source.read_text().replace('return value * multiplier', 'return 0'))\n        self.assertIsInstance",
        )
    )
    outcome = evaluate(tmp_path, "checkout", 0, random_seed=31)
    assert outcome["behavior_passed"]
    assert not outcome["checkout_unchanged_during_checks"]
    assert not outcome["qualified"]


def test_baseline_default_configuration_behavior_is_preserved(tmp_path):
    seed(tmp_path, "checkout", modular=True)
    run.write(tmp_path / "settings.json", {"multiplier": 99})
    outcome = evaluate(tmp_path, "checkout", 0, random_seed=17)
    assert not outcome["behavior_passed"]
    assert not outcome["qualified"]


def test_full_three_scenario_four_task_automation_is_explicitly_synthetic(tmp_path):
    config = run.parser().parse_args(["--synthetic", "--state", str(tmp_path)])
    with patch.object(
        run, "agent_call", side_effect=AssertionError("No live client in qualification")
    ):
        result = run.run_trial(config)
    assert result["status"] == "completed"
    assert result["evidence_kind"] == "synthetic"
    assert len(result["pairs"]) == 3
    assert not (tmp_path / "preflight.json").exists()
    for pair in result["pairs"]:
        control, treatment = pair["arms"]["control"], pair["arms"]["treatment"]
        assert len(control) == len(treatment) == 4
        assert control[0]["baseline_digest"] == treatment[0]["baseline_digest"]
        assert all(row["outcome"]["qualified"] for row in treatment)
        assert all(row["outcome"]["behavior_passed"] for row in control)
        assert all(not row["outcome"]["qualified"] for row in control)
        assert all(row["coding"]["tokens"] is None for row in treatment + control)
    labels = json.loads((tmp_path / "blind-map.private.json").read_text())
    assert len(labels) == 6
    for label in labels:
        bundle = tmp_path / "blind-review" / label
        assert (bundle / "tasks.json").is_file()
        assert not (bundle / "decisions.json").exists()
        assert not (bundle / "advice.private.json").exists()


def test_failed_real_capability_preflight_starts_no_pair(tmp_path):
    config = run.parser().parse_args(["--live", "--state", str(tmp_path), "--runtime", "/bin/true"])
    with (
        patch.object(run, "preflight", return_value={"status": "failed"}),
        patch.object(run, "freeze_advice") as advice,
    ):
        result = run.run_trial(config)
    assert result["status"] == "preflight_failed"
    assert result["pairs"] == []
    advice.assert_not_called()


def test_preflight_requires_real_success_specific_tools_and_server_effects(tmp_path):
    config = run.parser().parse_args(["--live", "--state", str(tmp_path), "--runtime", "/bin/true"])
    blocked = {
        "status": "completed",
        "non_tool_work": False,
        "tools": [
            {"server": "anaxigraph", "tool": name, "succeeded": False}
            for name in ("scan", "prepare_change", "inspect")
        ],
    }
    with (
        patch.object(run, "agent_call", return_value=blocked),
        patch.object(
            run, "ledger", return_value=[{"kind": "task_started"}, {"kind": "context_supplied"}]
        ),
    ):
        assert run.preflight(config)["status"] == "failed"


def test_context_and_adoption_need_exact_frozen_source_and_actual_patch(tmp_path):
    advice = {"reviews": [{"id": "saved", "source_digest": "correct"}]}
    notes = [
        {
            "advice_id": "saved",
            "disposition": "adopt",
            "reason": "isolate effects",
            "files": ["compute.py"],
        }
    ]
    run.write(tmp_path / "decisions.json", notes)
    call = {
        "server": "anaxigraph",
        "tool": "inspect",
        "succeeded": True,
        "arguments": {"entity_id": "review:saved"},
        "source_digest": "correct",
    }
    output = run.dispositions(tmp_path, advice, {"changed_paths": ["compute.py"]}, [call])[0]
    assert output["source_reference_verified"] and output["changed_source_evidence_verified"]
    assert "requires blinded" in output["semantic_adoption"]
    wrong_source = run.dispositions(
        tmp_path, advice, {"changed_paths": ["compute.py"]}, [call | {"source_digest": "wrong"}]
    )[0]
    assert not wrong_source["source_reference_verified"]
    no_patch = run.dispositions(tmp_path, advice, {"changed_paths": []}, [call])[0]
    assert (
        no_patch["source_reference_verified"] and not no_patch["changed_source_evidence_verified"]
    )


def test_commands_pin_same_resource_settings_and_new_contexts():
    for runtime in (None, Path("/runtime")):
        command = run.command(Path("/repo"), runtime)
        assert command[command.index("--model") + 1] == "gpt-6.1-sol"
        assert 'model_reasoning_effort="xhigh"' in command
        assert "--ephemeral" in command and "--ignore-rules" in command
        assert "mcp_servers={}" in command
        assert any("mcp_servers.anaxigraph.command=" in argument for argument in command) == (
            runtime is not None
        )
    config = run.parser().parse_args(["--synthetic", "--state", "/tmp/fixture"])
    assert config.call_timeout is None


def test_existing_frozen_trial_is_refused_without_mutating_its_record(tmp_path, monkeypatch):
    path = tmp_path / "run.private.json"
    path.write_text('{"status": "completed", "frozen": true}')
    before = path.read_bytes()
    monkeypatch.setattr("sys.argv", ["run", "--synthetic", "--state", str(tmp_path)])
    assert run.main() == 1
    assert path.read_bytes() == before


def preparation_fixture(tmp_path):
    import hashlib

    config = run.parser().parse_args(
        [
            "--live",
            "--runtime",
            "/bin/true",
            "--state",
            str(tmp_path),
            "--scenarios",
            "checkout",
            "--resume-preparation",
        ]
    )
    config.runtime = config.runtime.resolve()
    for arm in ("control", "treatment"):
        seed(tmp_path / "checkout-0" / arm, "checkout")
    source_root = tmp_path / "frozen-source"
    source_root.mkdir()
    (source_root / "version.py").write_text("VERSION = 1\n")
    run.write(
        tmp_path / "runtime-source.private.json",
        {"source_root": str(source_root), "files": run.source_hashes(source_root)},
    )
    run.write(
        tmp_path / "preflight.json",
        {
            "runtime": str(config.runtime),
            "runtime_sha256": hashlib.sha256(config.runtime.read_bytes()).hexdigest(),
        },
    )
    run.write(tmp_path / "invocation.private.json", run.invocation(config))
    run.write(
        tmp_path / "checkout-0/workflow-config.private.json", run.workflow_configuration(config)
    )
    previous = {
        "status": "incomplete",
        "pairs": [],
        "error_type": "RuntimeError",
        "error": "preparation failed",
    }
    run.write(tmp_path / "run.private.json", previous)
    return config, previous


def test_preparation_recovery_preserves_failure_and_requires_identical_frozen_inputs(tmp_path):
    config, previous = preparation_fixture(tmp_path)
    recovered = run.resume_preparation(config, previous)
    assert recovered["status"] == "qualifying"
    assert recovered["preparation_recoveries"][0]["prior_error"] == "preparation failed"
    assert config.preflight_attempt == 1
    assert (
        json.loads((tmp_path / "recovery-1.previous-run.private.json").read_text())["status"]
        == "incomplete"
    )


@pytest.mark.parametrize("change", ["source", "seed", "configuration", "coding_started"])
def test_preparation_recovery_cannot_add_unpaired_coding_or_change_inputs(tmp_path, change):
    config, previous = preparation_fixture(tmp_path)
    before = (tmp_path / "run.private.json").read_bytes()
    if change == "source":
        (tmp_path / "frozen-source/version.py").write_text("VERSION = 2\n")
    elif change == "seed":
        (tmp_path / "checkout-0/treatment/compute.py").write_text("def compute(*args): return 0\n")
    elif change == "configuration":
        config.stages = 2
    else:
        run.write(tmp_path / "checkout-0/treatment-evidence/task-0/baseline.private.json", {})
    with pytest.raises(ValueError):
        run.resume_preparation(config, previous)
    assert (tmp_path / "run.private.json").read_bytes() == before


@pytest.mark.parametrize("is_error", ["isError", "is_error"])
def test_completed_mcp_errors_are_not_success_or_consultation(is_error):
    event = {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "status": "completed",
            "server": "anaxigraph",
            "tool": "inspect",
            "result": {is_error: True, "content": [{"type": "text", "text": "failed"}]},
        },
    }
    assert not run.tool_calls([event])[0]["succeeded"]
