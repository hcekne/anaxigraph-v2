"""Run/export a retained 26-issue capability campaign, with no implicit model work."""

from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
import random
import sys
from pathlib import Path

from anaxigraph.analysis.ci_policy import plan_ci
from experiments.architecture_quality import campaign as provenance
from experiments.architecture_quality import run as coding

from . import delivery, fixture, oracle

MODEL, EFFORT = "gpt-6.1-sol", "high"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def runtime_identity():
    return {
        "python": sys.version,
        "executable": str(Path(sys.executable).resolve()),
        "platform": sys.platform,
        "planner_source_sha256": hashlib.sha256(
            Path(inspect.getfile(plan_ci)).read_bytes()
        ).hexdigest(),
        "oracle": fixture.digest(
            {path.name: path.read_text() for path in Path(__file__).parent.glob("*.py")}
        ),
    }


def plan(root, changed, runtime):
    return plan_ci(
        json.loads((root / "anaxigraph/ci-policy.json").read_text()),
        changed,
        oracle.snapshot(root),
        runtime_context=runtime,
    )


def selected(plan):
    return [row["id"] for row in plan["selected_targets"]]


def promote(checkpoint, root, spec, outcome, runtime, tree, *, completed=True):
    """Only a verified successful release advances deployment and CI baselines."""
    checkpoint["retained"] = outcome.get("retained") or checkpoint.get("retained")
    if not outcome["qualified"] or not completed:
        return False
    checkpoint.update(
        baseline=oracle.snapshot(root),
        artifacts=delivery.artifacts(root, spec, runtime),
        release_stage=spec["stage"],
        release_state=copy.deepcopy(checkpoint["retained"]),
    )
    delivery.preserve(root, tree)
    return True


def packet(ids, stage, *, arm="control", advice=None):
    context = (
        coding.CONTROL_RULE
        if arm == "control"
        else (
            "Use the attached AnaxiGraph MCP: scan, prepare_change, assess_plan, then validate actual "
            "work against the task baseline. Inspect relevant saved reviews, record material decisions "
            "with reasons. No new model workflows; the coordinator runs milestone reviews."
        )
    )
    if advice:
        context += "\nAvailable review metadata:\n" + json.dumps(
            [
                {"id": row["id"], "kind": row["kind"], "inspect": "review:" + row["id"]}
                for row in advice["reviews"]
                if row["kind"] in coding.USEFUL_REVIEWS
            ]
        )
    return {
        "contract": "drift-coding-request-v1",
        "model": MODEL,
        "reasoning_effort": EFFORT,
        "stage": stage,
        "brief": fixture.brief(ids),
        "earlier_requirements": [fixture.issue(index, ids) for index in range(stage)],
        "task": fixture.issue(stage, ids),
        "instructions": context
        + "\nImplement this issue in source, preserving prior behavior. Use a fresh coding context. "
        "No hidden tests, future tasks, private coordinator files, commits or external services. "
        "The evaluator will execute retained verification independently. Do not edit checks.py: "
        "its immutable adapter binds evaluator-owned capabilities, not a replaceable assertion stub. "
        "Write the strict submission.json proposal described in the brief. Actual declared checks "
        "and artifact identities are recorded by the coordinator; self-reported passes do not count.",
    }


def qualify(state, *, components=12, integrations=48, seed=7103, episodes=fixture.COUNT):
    if state.exists():
        raise ValueError("Use a new qualification directory")
    state.mkdir(parents=True)
    ids, runtime = fixture.identities(components, integrations, seed), runtime_identity()
    root = state / "candidate"
    prior = fixture.specification(-1, ids)
    fixture.write(root, fixture.files(prior))
    retained, previous_artifacts = None, delivery.artifacts(root, prior, runtime)
    previous_tree = state / "previous-artifact"
    delivery.preserve(root, previous_tree)
    rows, controls, executed_controls = [], [], []
    for stage in range(episodes):
        spec = fixture.specification(stage, ids)
        changed = fixture.advance(root, prior, spec, coupled=False, previous_coupled=stage == 0)
        planned = plan(root, changed, runtime)
        proposed = selected(planned)
        evidence = delivery.receipt(root, spec, proposed, runtime, previous_artifacts)
        outcome = oracle.evaluate(root, spec, proposed, planned, retained, evidence=evidence)
        if stage == 0:
            for label, scope, receipt in (
                ("empty-selection", [], None),
                ("always-all", oracle.checks(spec), None),
                (
                    "stale-evidence",
                    proposed,
                    {"candidate_digest": "old", "check_ids": sorted(proposed)},
                ),
            ):
                control = oracle.evaluate(root, spec, scope, planned, retained, evidence=receipt)
                expected = (
                    control["qualified"] if label == "always-all" else not control["qualified"]
                )
                if label == "always-all":
                    expected = (
                        expected and control["synthetic_cost_model"]["efficiency_credit"] == 0
                    )
                executed_controls.append(
                    {
                        "id": label,
                        "expected_result_observed": expected,
                        "qualified": control["qualified"],
                        "hard_failures": control["hard_failures"],
                    }
                )
                write(state / "controls" / (label + ".json"), control)
        current_artifacts = delivery.artifacts(root, spec, runtime)
        deploy = [
            key
            for key, value in current_artifacts.items()
            if previous_artifacts[key]["digest"] != value["digest"]
        ]
        proposal = {"selected": proposed, "deploy": deploy, "unknowns": []}
        recovery = (
            oracle.run(previous_tree, prior, oracle.checks(prior), retained)
            if spec["rollout"]
            else None
        )
        rollout = delivery.verify(
            root,
            spec,
            proposal,
            evidence,
            runtime,
            previous_artifacts,
            recovery=recovery,
            recovery_digest=fixture.digest(oracle.snapshot(previous_tree)) if recovery else None,
        )
        outcome["delivery"] = rollout
        if not rollout["passed"]:
            outcome["qualified"] = False
            outcome["synthetic_cost_model"]["efficiency_credit"] = None
        if not outcome["qualified"]:
            rollout["synthetic_deployment_work"]["work_avoided_credit"] = None
        # Always-all is a safe selection control, with exactly zero selection savings.
        all_proposal = oracle.checks(spec)
        broad_safe = (
            set(outcome["required_checks"]) <= set(all_proposal)
            and not outcome["missing_checks"]
            and all(row["passed"] for row in outcome["full"]["checks"].values())
        )
        controls.append(
            {
                "stage": stage,
                "always_all_safe": broad_safe,
                "always_all_efficiency_credit": 0,
                "empty_selection_omits_obligations": bool(set(outcome["required_checks"])),
                "mutant_fails_full": any(
                    not row["passed"] for row in outcome["fail_full_mutant"]["checks"].values()
                ),
            }
        )
        row = {
            "stage": stage,
            "task": fixture.issue(stage, ids),
            "changed_paths": changed,
            "plan": planned,
            "outcome": outcome,
        }
        rows.append(row)
        write(state / "episodes" / f"{stage:02d}.json", row)
        retained = outcome["retained"] or retained
        previous_artifacts, prior = current_artifacts, spec
        delivery.preserve(root, previous_tree)
    passed = (
        all(row["outcome"]["qualified"] for row in rows)
        and all(row["expected_result_observed"] for row in executed_controls)
        and all(row["always_all_safe"] and row["mutant_fails_full"] for row in controls)
    )
    report = {
        "contract": "long-horizon-drift-v1",
        "status": "passed" if passed else "failed",
        "evidence_kind": "deterministic-evaluator-qualification",
        "model_calls": 0,
        "episodes": episodes,
        "components": components,
        "integrations": integrations,
        "seed": seed,
        "runtime": runtime,
        "outcomes": [
            {
                "stage": row["stage"],
                "qualified": row["outcome"]["qualified"],
                "failures": row["outcome"]["hard_failures"],
                "delivery_failures": row["outcome"]["delivery"]["failures"],
                "observed_work": row["outcome"]["observed_work"],
                "synthetic_cost_model": row["outcome"]["synthetic_cost_model"],
                "synthetic_deployment_work": row["outcome"]["delivery"][
                    "synthetic_deployment_work"
                ],
            }
            for row in rows
        ],
        "negative_controls": executed_controls,
        "per_episode_fault_controls": controls,
        "claims": [
            "Actual sequential source changes, externally executed contract checks, fail-full mutations, opaque retained state and local artifact/recovery identities",
            "No live coding-agent outcome, production CI speedup, hosted deployment or architectural superiority is established",
        ],
    }
    write(state / "qualification.json", report)
    return report


def initialize(state, *, components, integrations, seed):
    if state.exists():
        raise ValueError("Use a new campaign directory; no favorable silent reruns")
    ids = fixture.identities(components, integrations, seed)
    state.mkdir(parents=True)
    runtime = runtime_identity()
    roots = {arm: state / arm for arm in ("treatment", "control")}
    for root in roots.values():
        fixture.write(root, fixture.files(fixture.specification(-1, ids)))
    if oracle.snapshot(roots["treatment"]) != oracle.snapshot(roots["control"]):
        raise ValueError("Pair seed identity mismatch")
    return ids, runtime, roots


def export(state, *, components=12, integrations=48, seed=7103):
    ids, runtime, roots = initialize(
        state, components=components, integrations=integrations, seed=seed
    )
    manifest = {
        "contract": "drift-external-session-v1",
        "status": "awaiting-stage-0",
        "model": MODEL,
        "reasoning_effort": EFFORT,
        "ids": ids,
        "runtime": runtime,
        "stage": 0,
        "arms": {},
    }
    for arm, root in roots.items():
        manifest["arms"][arm] = {
            "baseline": oracle.snapshot(root),
            "retained": None,
            "artifacts": delivery.artifacts(root, fixture.specification(-1, ids), runtime),
            "release_stage": -1,
            "release_state": None,
        }
        delivery.preserve(root, state / f"{arm}-previous-artifact")
        write(root / "TASK.json", packet(ids, 0, arm=arm))
    write(state / "session.private.json", manifest)
    return {
        "status": manifest["status"],
        "candidate_roots": {key: str(value) for key, value in roots.items()},
        "model_calls": 0,
        "note": "External runner must isolate private session/oracle from agents; TASK.json contains only current/earlier issues",
    }


def assess(
    root,
    spec,
    before,
    previous,
    previous_artifacts,
    runtime,
    previous_tree,
    *,
    release_stage=None,
    release_state=None,
):
    proposal = oracle.submission(json.loads((root / "submission.json").read_text()), spec)
    changed = coding.delta(before, oracle.snapshot(root))["changed_paths"]
    planned = plan(root, changed, runtime)
    evidence = delivery.receipt(root, spec, proposal["selected"], runtime, previous_artifacts)
    outcome = oracle.evaluate(
        root, spec, proposal["selected"], planned, previous, evidence=evidence
    )
    prior = fixture.specification(
        spec["stage"] - 1 if release_stage is None else release_stage, spec["ids"]
    )
    recovery = (
        oracle.run(
            previous_tree,
            prior,
            oracle.checks(prior),
            previous if release_stage is None else release_state,
        )
        if spec["rollout"]
        else None
    )
    rollout = delivery.verify(
        root,
        spec,
        proposal,
        evidence,
        runtime,
        previous_artifacts,
        recovery=recovery,
        recovery_digest=fixture.digest(oracle.snapshot(previous_tree)) if recovery else None,
    )
    outcome["delivery"] = rollout
    if proposal["unknowns"]:
        outcome["hard_failures"].append("declared_unknown_scope")
    outcome["qualified"] = outcome["qualified"] and rollout["passed"] and not proposal["unknowns"]
    if not outcome["qualified"]:
        outcome["synthetic_cost_model"]["efficiency_credit"] = None
        rollout["synthetic_deployment_work"]["work_avoided_credit"] = None
    return {
        "plan": planned,
        "proposal": proposal,
        "changed_paths": changed,
        "evidence": evidence,
        "outcome": outcome,
    }


def grade(state):
    path = state / "session.private.json"
    manifest = json.loads(path.read_text())
    if (
        manifest.get("contract") != "drift-external-session-v1"
        or manifest["stage"] >= fixture.COUNT
    ):
        raise ValueError("No pending external episode")
    if manifest["runtime"] != runtime_identity():
        raise ValueError("Evaluator/runtime drift; start a new frozen session")
    stage = manifest["stage"]
    spec = fixture.specification(stage, manifest["ids"])
    failed = []
    for arm, previous in manifest["arms"].items():
        root = state / arm
        try:
            row = assess(
                root,
                spec,
                previous["baseline"],
                previous["retained"],
                previous["artifacts"],
                manifest["runtime"],
                state / f"{arm}-previous-artifact",
                release_stage=previous["release_stage"],
                release_state=previous["release_state"],
            )
        except (ValueError, OSError, KeyError) as error:
            row = {
                "outcome": {
                    "qualified": False,
                    "error": str(error),
                    "retained": previous["retained"],
                }
            }
        write(state / "results" / arm / f"{stage:02d}.json", row)
        if not row["outcome"]["qualified"]:
            failed.append(arm)
        row["release_promoted"] = promote(
            previous,
            root,
            spec,
            row["outcome"],
            manifest["runtime"],
            state / f"{arm}-previous-artifact",
        )
        write(state / "results" / arm / f"{stage:02d}.json", row)
        (root / "submission.json").unlink(missing_ok=True)
        if stage + 1 < fixture.COUNT:
            write(root / "TASK.json", packet(manifest["ids"], stage + 1, arm=arm))
    manifest["stage"] += 1
    manifest["status"] = (
        "completed" if manifest["stage"] == fixture.COUNT else f"awaiting-stage-{manifest['stage']}"
    )
    write(path, manifest)
    return {
        "status": manifest["status"],
        "graded_stage": stage,
        "episode_passed": not failed,
        "failed_arms": failed,
        "note": "Failed attempts retained; next source/state continues without reference repair",
    }


def live(config):
    """Reuse the existing Codex launcher, preflight, actual advice and receipt parser."""
    if config.agent != "codex":
        raise ValueError(
            "Live requires the standard Codex client; use external export for custom agents"
        )
    if not config.runtime or not config.runtime.is_file():
        raise ValueError("Live requires an installed AnaxiGraph runtime")
    ids, runtime, roots = initialize(
        config.state,
        components=config.components,
        integrations=config.integrations,
        seed=config.seed,
    )
    config.synthetic_transport = False
    config.live = True
    config.analysis_workers = 4
    config.coding_model, config.coding_effort = MODEL, EFFORT
    client = coding.client_identity(config)
    if not client["available"]:
        raise ValueError("Codex client unavailable")
    frozen_source = provenance.source_identity(Path(__file__).resolve().parents[2])
    manifest = {
        "contract": "drift-live-pair-v1",
        "status": "preparing",
        "requested_model": MODEL,
        "requested_effort": EFFORT,
        "runtime": runtime,
        "anaxigraph_source": frozen_source,
        "client": client,
        "config": {
            "components": config.components,
            "integrations": config.integrations,
            "seed": config.seed,
            "episodes": config.episodes,
            "call_timeout": config.call_timeout,
        },
        "arms": {},
    }
    write(config.state / "run.private.json", manifest)
    preflight = coding.preflight(config)
    write(config.state / "preflight.private.json", preflight)
    if preflight.get("status") != "passed":
        manifest["status"] = "delivery_preflight_failed"
        write(config.state / "run.private.json", manifest)
        return manifest
    advice = coding.freeze_advice(config, roots["treatment"], config.state / "analysis" / "initial")
    previous_spec = fixture.specification(-1, ids)
    checkpoints = {
        arm: {
            "baseline": oracle.snapshot(root),
            "retained": None,
            "artifacts": delivery.artifacts(root, previous_spec, runtime),
            "release_stage": -1,
            "release_state": None,
        }
        for arm, root in roots.items()
    }
    for arm, root in roots.items():
        delivery.preserve(root, config.state / f"{arm}-previous-artifact")
        manifest["arms"][arm] = []
    rng = random.Random(config.seed)
    for stage in range(config.episodes):
        if stage and stage % 8 == 0:
            advice = coding.freeze_advice(
                config, roots["treatment"], config.state / "analysis" / f"milestone-{stage}"
            )
        order = list(roots)
        rng.shuffle(order)
        for arm in order:
            root = roots[arm]
            checkpoint = checkpoints[arm]
            before = oracle.snapshot(root)
            directory = config.state / "evidence" / arm / f"{stage:02d}"
            request = packet(ids, stage, arm=arm, advice=advice if arm == "treatment" else None)
            write(directory / "request.private.json", request)
            call = coding.agent_call(
                root,
                directory,
                config.runtime if arm == "treatment" else None,
                json.dumps(request),
                config,
            )
            try:
                row = assess(
                    root,
                    fixture.specification(stage, ids),
                    checkpoint["baseline"],
                    checkpoint["retained"],
                    checkpoint["artifacts"],
                    runtime,
                    config.state / f"{arm}-previous-artifact",
                    release_stage=checkpoint["release_stage"],
                    release_state=checkpoint["release_state"],
                )
            except (ValueError, OSError, KeyError) as error:
                row = {
                    "outcome": {
                        "qualified": False,
                        "error": str(error),
                        "retained": checkpoint["retained"],
                    }
                }
            row["coding"] = call
            row["task_patch"] = coding.delta(before, oracle.snapshot(root))
            row["requested_model"] = MODEL
            row["requested_effort"] = EFFORT
            row["model_identity_basis"] = (
                "Requested CLI configuration and retained raw client receipts; not signed model attestation"
            )
            if arm == "control" and (call.get("control_isolation") or {}).get("violations"):
                row["outcome"]["qualified"] = False
            row["release_promoted"] = promote(
                checkpoint,
                root,
                fixture.specification(stage, ids),
                row["outcome"],
                runtime,
                config.state / f"{arm}-previous-artifact",
                completed=call["status"] == "completed",
            )
            manifest["arms"][arm].append(row)
            write(directory / "result.private.json", row)
            (root / "submission.json").unlink(missing_ok=True)
            write(config.state / "run.private.json", manifest)
    manifest["status"] = (
        "completed"
        if all(
            row["outcome"]["qualified"] and row["coding"]["status"] == "completed"
            for rows in manifest["arms"].values()
            for row in rows
        )
        else "finished_with_failures"
    )
    write(config.state / "run.private.json", manifest)
    return manifest


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("qualify", "export", "grade", "live"))
    result.add_argument("--state", type=Path, required=True)
    result.add_argument("--components", type=int, default=12)
    result.add_argument("--integrations", type=int, default=48)
    result.add_argument("--seed", type=int, default=7103)
    result.add_argument(
        "--episodes", type=int, choices=range(1, fixture.COUNT + 1), default=fixture.COUNT
    )
    result.add_argument("--runtime", type=Path)
    result.add_argument("--agent", default="codex")
    result.add_argument("--call-timeout", type=int, default=None)
    return result


def main():
    args = parser().parse_args()
    args.state = args.state.resolve()
    if args.runtime:
        args.runtime = args.runtime.resolve()
    source = Path(__file__).resolve().parents[2]
    if source == args.state or source in args.state.parents:
        raise ValueError("Keep agent trees and private evaluation outside the source checkout")
    if args.command == "qualify":
        result = qualify(
            args.state,
            components=args.components,
            integrations=args.integrations,
            seed=args.seed,
            episodes=args.episodes,
        )
    elif args.command == "export":
        result = export(
            args.state, components=args.components, integrations=args.integrations, seed=args.seed
        )
    elif args.command == "grade":
        result = grade(args.state)
    else:
        result = live(args)
    print(
        json.dumps(
            {
                "status": result["status"],
                "state": str(args.state),
                "model_calls": result.get("model_calls"),
            }
        )
    )
    return int(
        bool(result.get("failed_arms"))
        or result["status"] in {"failed", "finished_with_failures", "delivery_preflight_failed"}
    )


if __name__ == "__main__":
    raise SystemExit(main())
