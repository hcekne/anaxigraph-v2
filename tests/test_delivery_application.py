"""CI declarations guide real checkout changes without running project commands."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from test_workflows import answer

from anaxigraph.application import Application, delivery
from anaxigraph.workflows.engine import WorkflowRunner


def write(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


@pytest.fixture
def application(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    for path, text in {
        "payments/charge.py": "def calculate(amount):\n    return amount + 1\n",
        "payments/api.py": "def charge(amount: int) -> int:\n    return amount\n",
        "payments/obsolete.py": "def old():\n    return None\n",
        "checkout/client.py": "from payments.api import charge\ndef buy():\n    return charge(1)\n",
        "kitchen/fan.py": "def speed():\n    return 1\n",
        "tests/test_contracts.py": "def test_contract():\n    assert True\n",
        "scripts/ci.sh": "#!/bin/sh\necho check\n",
        "uv.lock": "version = 1\n",
        ".github/workflows/checks.yml": "jobs:\n  checks:\n    steps:\n      - run: sh scripts/ci.sh\n",
        ".github/workflows/release.yml": "jobs:\n  release:\n    steps:\n      - run: echo release\n",
        ".gitignore": ".anaxigraph-v2/\nanaxigraph/record.json\n",
    }.items():
        write(root, path, text)
    policy = {
        "version": 1,
        "targets": [
            {
                "id": "payments",
                "command": "touch SHOULD_NOT_EXECUTE",
                "inputs": ["payments/**"],
                "needs": ["contracts"],
                "estimated_seconds": 15,
                "cache": True,
            },
            {
                "id": "contracts",
                "command": "pytest tests/test_contracts.py",
                "inputs": ["tests/test_contracts.py"],
                "estimated_seconds": 1,
                "cache": True,
            },
            {
                "id": "public-api",
                "command": "python -m build",
                "inputs": ["payments/api.py"],
                "cadence": "release",
                "kind": "build",
                "estimated_seconds": 2,
                "cache": True,
            },
            {
                "id": "checkout",
                "command": "pytest checkout",
                "inputs": ["checkout/**"],
                "consumes": ["public-api"],
                "estimated_seconds": 3,
                "cache": True,
            },
            {"id": "kitchen", "command": "pytest kitchen", "inputs": ["kitchen/**"]},
            {
                "id": "full",
                "command": "pytest",
                "inputs": ["payments/**", "checkout/**", "kitchen/**", "tests/**"],
                "cadence": "scheduled",
            },
        ],
        "fallback": ["full"],
        "shared_inputs": ["uv.lock", "anaxigraph/ci-policy.json"],
    }
    write(root, delivery.POLICY_PATH, json.dumps(policy))
    app = Application(root)
    app.scan()
    return app


def selected(result):
    return {item["id"] for item in result["selected_targets"]}


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_declared_payment_body_check_is_narrow_sixteen_seconds_and_never_executes(application):
    plan = application.ci_plan(
        changed_paths=["payments/charge.py"], runtime_context={"python": "3.12"}
    )
    assert selected(plan) == {"contracts", "payments"}
    assert plan["layers"] == [["contracts"], ["payments"]]
    assert plan["critical_path_seconds"] == 16
    assert plan["comparison"]["basis"] == "caller-supplied-paths"
    assert plan["comparison"]["completeness"] == "caller-reported"
    assert plan["execution_ready"] and not plan["checks_executed"]
    assert all(item["cache_eligible"] for item in plan["selected_targets"])
    assert not (application.root / "SHOULD_NOT_EXECUTE").exists()


def test_declared_public_contract_widens_to_its_consumers_not_other_components(application):
    plan = application.ci_plan(changed_paths=["payments/api.py"])
    assert selected(plan) == {"contracts", "payments", "public-api", "checkout"}
    assert plan["layers"].index(["contracts", "public-api"]) == 0
    assert not plan["checks_executed"]


def test_shared_toolchain_change_expands_change_checks_with_periodic_checks_retained(application):
    plan = application.ci_plan(changed_paths=["uv.lock"])
    assert selected(plan) == {"payments", "contracts", "checkout", "kitchen", "public-api"}
    assert plan["reevaluation_required"]
    assert "full" in plan["retained_periodic"]
    assert not plan["checks_executed"]


def test_missing_and_invalid_policy_are_explicit_and_advisory_preserves_unknown(application):
    policy = application.root / delivery.POLICY_PATH
    policy.unlink()
    application.scan()
    result = application.ci_plan()
    assert result["status"] == "not_configured"
    assert not result["execution_ready"] and not result["checks_executed"]
    write(application.root, delivery.POLICY_PATH, '{"version": 1, "targets": []}')
    application.scan()
    with pytest.raises(ValueError, match="Invalid CI policy"):
        application.ci_plan(changed_paths=["payments/charge.py"])
    result = delivery.advisory(application, changed_paths=["payments/charge.py"])
    assert result["status"] == "unresolved" and "Invalid CI policy" in result["reason"]
    assert not result["execution_ready"] and not result["checks_executed"]


def test_ci_comparison_requires_a_pin_and_does_not_drift_after_repeated_scans(application):
    path = application.root / "payments/charge.py"
    path.write_text(path.read_text().replace("+ 1", "+ 2"))
    application.scan()
    with pytest.raises(ValueError, match="explicit base, pinned task baseline"):
        application.ci_plan()
    unpinned = application.validate_change()
    assert unpinned["baseline_basis"] == "previous-scan"
    assert unpinned["ci_plan"]["status"] == "unresolved"
    assert not unpinned["ci_plan"]["execution_ready"]
    baseline = application.prepare_change("change payment calculation")["baseline_id"]
    path.write_text(path.read_text().replace("+ 2", "+ 3"))
    application.scan()
    write(application.root, "kitchen/fan.py", "def speed():\n    return 2\n")
    application.scan()
    result = application.ci_plan()
    assert result["changed_paths"] == ["kitchen/fan.py", "payments/charge.py"]
    assert result["comparison"] == {
        "basis": "pinned-change-baseline",
        "base_snapshot_id": baseline,
    }
    explicit = application.ci_plan(base_snapshot_id=baseline)
    assert explicit["changed_paths"] == result["changed_paths"]
    assert explicit["comparison"]["basis"] == "explicit-snapshot"


def test_agent_plan_and_validation_receive_actionable_ci_scope_before_and_after_edit(application):
    proposed = application.assess_plan("improve payment calculation", ["payments/charge.py"])
    assert selected(proposed["proposed_ci_plan"]) == {"payments", "contracts"}
    assert not proposed["proposed_ci_plan"]["checks_executed"]
    baseline = proposed["brief"]["baseline_id"]
    write(application.root, "payments/charge.py", "def calculate(amount):\n    return amount + 2\n")
    application.scan()
    validated = application.validate_change()
    assert selected(validated["ci_plan"]) == {"payments", "contracts"}
    assert validated["ci_plan"]["comparison"]["base_snapshot_id"] == baseline
    assert not validated["ci_plan"]["checks_executed"]


def test_task_brief_includes_specific_saved_ci_advice_without_a_ci_keyword(application):
    request = application.analysis_request("delivery")
    result = answer(request)
    identity = request["packet"]["pipeline_ids"][0]
    result["assessments"][0]["outcome"] = "finding"
    result["recommendations"] = [
        {
            "pipeline_ids": [identity],
            "priority": 2,
            "title": "Publish component-owned verification commands",
            "bottleneck": "The captured command invokes an opaque script without declared job inputs.",
            "fast_path": ["Use the declared payment implementation and contract checks."],
            "broaden_when": ["Public payment contract or shared toolchain changes."],
            "cache_strategy": ["Include task inputs and runtime in fingerprints."],
            "parallelism": ["Run independent component checks concurrently."],
            "deployment_checks": ["Retain consumer compatibility checks before deployment."],
            "retain_checks": ["Keep the scheduled full suite."],
            "next_steps": ["Measure the declared payment checks in the existing CI job."],
            "evidence": [{"path": identity, "line": 1}],
            "uncertainty": ["No CI checks were executed for this synthetic review."],
        }
    ]
    application.submit_analysis(request["request_id"], result)
    brief = application.prepare_change("adjust payment rounding")
    advice = brief["delivery"]
    assert advice["status"] == "compatible_design_review"
    assert advice["suggestions"][0]["title"] == result["recommendations"][0]["title"]
    assert advice["suggestions"][0]["next_step"] == result["recommendations"][0]["next_steps"][0]
    assert advice["suggestions"][0]["expand"] == "review:" + request["request_id"]
    assert "no checks were executed" in advice["basis"]


def test_explicit_git_base_includes_deletions_untracked_and_unindexed_tracked_files(application):
    root = application.root
    (root / "opaque.bin").write_bytes(b"\0old")
    git(root, "init", "-q")
    git(root, "config", "user.email", "fixture@example.test")
    git(root, "config", "user.name", "Fixture")
    git(root, "add", ".")
    git(root, "commit", "-qm", "baseline")
    base = git(root, "rev-parse", "HEAD")
    (root / "payments/obsolete.py").unlink()
    write(root, "payments/fresh.py", "def fresh():\n    return 1\n")
    (root / "opaque.bin").write_bytes(b"\0new")
    application.scan()
    result = application.ci_plan(base_ref="HEAD")
    assert result["changed_paths"] == [
        "opaque.bin",
        "payments/fresh.py",
        "payments/obsolete.py",
    ]
    assert result["comparison"]["base_commit"] == base
    assert result["status"] == "incomplete"
    assert result["uncovered_paths"] == ["opaque.bin"]
    assert "full" in selected(result) and not result["execution_ready"]


def test_ambiguous_or_invalid_comparison_and_unscanned_changes_fail_without_execution(application):
    with pytest.raises(ValueError, match="Select one CI comparison"):
        application.ci_plan(base_ref="HEAD", changed_paths=[])
    with pytest.raises(ValueError, match="local Git revision"):
        application.ci_plan(base_ref="--output=SHOULD_NOT_EXECUTE")
    write(application.root, "payments/charge.py", "raise RuntimeError('never execute source')\n")
    with pytest.raises(ValueError, match="Checkout changed"):
        application.ci_plan(changed_paths=["payments/charge.py"])
    assert not (application.root / "SHOULD_NOT_EXECUTE").exists()


def test_root_and_filtered_reviews_survive_portable_clone_with_explicit_coverage(
    application, tmp_path
):
    root_request = application.analysis_request("delivery")
    application.submit_analysis(root_request["request_id"], answer(root_request))
    paths = [".github/workflows/checks.yml"]
    filtered = application.analysis_request("delivery", pipeline_paths=paths)
    application.submit_analysis(filtered["request_id"], answer(filtered))
    before = delivery.advice(application, application._current())
    assert before["scope_status"] == "filtered" and before["pipeline_paths"] == paths
    assert before["coverage"]["pipeline_ids_total"] == len(paths)
    application.export_record()
    root = tmp_path / "another-machine" / "checkout"
    shutil.copytree(application.root, root, ignore=shutil.ignore_patterns(".anaxigraph-v2", ".git"))
    cloned = Application(root)
    imported = cloned.scan()["portable_import"]
    assert imported["analyses_imported"] == 2
    rows = cloned.store.reviews(str(root), "delivery", include_requests=False)
    assert {tuple(row["result"]["request_options"]["pipeline_paths"] or []) for row in rows} == {
        (),
        tuple(paths),
    }
    assert all(row["result"]["delivery_coverage"]["pipeline_ids"] for row in rows)
    assert all(row["result"]["provenance"]["source_snapshot"] for row in rows)


def test_filtered_ci_scope_stays_compact_and_exposes_preview_limits(application):
    paths = [f".github/workflows/{number:02}-{'component' * 16}.yml" for number in range(16)]
    for path in paths:
        write(application.root, path, "jobs:\n  check:\n    steps:\n      - run: echo test\n")
    application.scan()
    request = application.analysis_request("delivery", pipeline_paths=paths)
    application.submit_analysis(request["request_id"], answer(request))
    advice = application.prepare_change("adjust payment rounding")["delivery"]
    assert advice["scope_status"] == "filtered"
    assert advice["pipeline_paths"] == paths[:3]
    assert advice["pipeline_paths_total"] == 16 and advice["pipeline_paths_truncated"]
    assert advice["coverage"]["pipeline_ids_total"] == 16
    assert set(advice["coverage"]) == {"partial_evidence", "pipeline_ids_total", "pipelines_total"}
    assert len(json.dumps(advice).encode()) < 1500


def test_ci_design_review_reuses_body_only_edit_without_another_model_call(application):
    calls = []

    def provider(request, *_args, **_kwargs):
        calls.append(request["kind"])
        return answer(request)

    runner = WorkflowRunner(application, provider)
    first = runner.start("ci", background=False)
    assert first["status"] == "completed", first
    path = application.root / "payments/charge.py"
    path.write_text(path.read_text().replace("+ 1", "+ 2"))
    application.scan()
    second = runner.start("ci", background=False)
    assert second["status"] == "completed" and second["reused_batches"] == 1, second
    assert calls == ["delivery"]
    assert (
        delivery.advice(application, application._current())["status"] == "compatible_design_review"
    )


@pytest.mark.parametrize(
    "path", [".github/workflows/checks.yml", "scripts/ci.sh", delivery.POLICY_PATH]
)
def test_ci_script_and_policy_changes_invalidate_saved_design_review(application, path):
    calls = []

    def provider(request, *_args, **_kwargs):
        calls.append(request["kind"])
        return answer(request)

    runner = WorkflowRunner(application, provider)
    assert runner.start("ci", background=False)["status"] == "completed"
    target = application.root / path
    if path == delivery.POLICY_PATH:
        policy = json.loads(target.read_text())
        policy["targets"][0]["estimated_seconds"] = 18
        target.write_text(json.dumps(policy))
    else:
        target.write_text(target.read_text() + "\n# changed declaration or script\n")
    application.scan()
    assert delivery.advice(application, application._current())["status"] == "stale"
    result = runner.start("ci", background=False)
    assert result["status"] == "completed", result
    assert result["reused_batches"] == 0 and calls == ["delivery", "delivery"]


def test_advisory_reports_missing_map_without_suggesting_success(tmp_path):
    result = delivery.advisory(Application(tmp_path), changed_paths=["service.py"])
    assert result["status"] == "unresolved" and "No map" in result["reason"]
    assert not result["execution_ready"] and not result["checks_executed"]
