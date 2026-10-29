"""Freeze a small usefulness campaign and its commands without launching model work."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import run
except ImportError:
    import run


def source_identity(root: Path) -> dict:
    """Record maintained inputs, including uncommitted additions, without hashing a venv."""
    paths = (
        subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
        )
        .decode()
        .split("\0")
    )
    files = {
        path: hashlib.sha256((root / path).read_bytes()).hexdigest()
        for path in sorted(set(paths))
        if path
        and (
            path.startswith(("src/", "experiments/", "scripts/"))
            or path in {"pyproject.toml", "uv.lock", "quality-budget.json"}
        )
        and (root / path).is_file()
    }
    return {
        "git_head": subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip(),
        "files": files,
        "files_sha256": run.digest(files),
    }


def build(
    root: Path,
    state_root: Path,
    *,
    replicates: int = 3,
    random_seed: int = 20261029,
    plan_path: Path | None = None,
):
    if type(replicates) is not int or replicates < 1:
        raise ValueError("replicates must be a positive integer")
    root, state_root = root.resolve(), state_root.resolve()
    if state_root == root or root in state_root.parents:
        raise ValueError("Keep candidate checkouts, private logs and plans outside the source repo")
    runtime = root / ".venv/bin/anaxigraph"
    if not runtime.is_file():
        raise ValueError("Install this checkout first: uv sync --locked --extra dev")

    def command(name, scenario, mode, *, synthetic=False, count=replicates):
        argv = [
            sys.executable,
            "-m",
            "experiments.architecture_quality.run",
            "--synthetic" if synthetic else "--live",
            "--track",
            "discovery",
            "--scenarios",
            scenario,
            "--control-mode",
            mode,
            "--replicates",
            str(count),
            "--random-seed",
            str(random_seed),
            "--state",
            str(state_root / name),
        ]
        if not synthetic:
            argv += ["--runtime", str(runtime), "--runtime-source-root", str(root / "src")]
        if plan_path is not None:
            argv += ["--campaign-plan", str(plan_path.resolve())]
        return {"cwd": str(root), "argv": argv, "shell": shlex.join(argv)}

    comparisons = [
        ("cohesive-normal", "workshop", "normal", "Unnecessary advice on an already cohesive app"),
        ("coupled-normal", "workshop-coupled", "normal", "Useful discovery under genuine coupling"),
        (
            "coupled-review",
            "workshop-coupled",
            "review",
            "Structured advice versus additional ordinary source review",
        ),
    ]
    preflight = command("preflight", "workshop-coupled", "normal", count=1)
    preflight["argv"].append("--preflight-only")
    preflight["shell"] = shlex.join(preflight["argv"])
    return {
        "contract": "usefulness-campaign-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "planned-not-run",
        "runtime_source": source_identity(root),
        "runtime_executable": str(runtime),
        "runtime_executable_sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
        "model": run.MODEL,
        "reasoning_effort": run.EFFORT,
        "random_seed": random_seed,
        "replicates_per_comparison": replicates,
        "stages_per_arm": 4,
        "planned_coding_turns": len(comparisons) * replicates * 2 * 4,
        "analysis_call_count": None,
        "cost_usd": None,
        "cost_basis": "Coding opportunities matched; total analysis/tokens are measured, not equal",
        "design": {
            "unit": "paired four-task chain; task results within a chain are dependent",
            "cohesive_control": "workshop is a negative control, not an effectiveness hurdle",
            "coupled_seed": "constructed behavioral counterpart, not a held-out real project",
            "primary": "independent behavior/CI outcomes and frozen blinded architecture judgments",
            "secondary": "later-change effort, advice consultation/adoption and total resources",
            "reviewers": "two fresh subagents, fork_turns=none, bundles and rubric only",
            "unblinding": "freeze source-evidenced judgments before assignments, time and tokens",
            "inference": "exploratory paired descriptions; no powered or general causal claim",
            "failure_policy": "retain every attempt; no extra coding repair turns or favorable reruns",
            "delivery_failure": "invalid delivery is not a measurement of useful delivered advice",
            "stop_rule": "stop at predeclared replicates, or retain incomplete/failed status",
            "external_validation": "freeze this protocol before selecting natural external tasks",
        },
        "qualification": [
            command("rehearsal-" + scenario, scenario, "normal", synthetic=True, count=1)
            for scenario in run.DISCOVERY_SCENARIOS
        ],
        "preflight": preflight,
        "pilot": command("pilot-coupled", "workshop-coupled", "normal", count=1),
        "comparisons": [
            {
                "id": name,
                "question": question,
                "command": command(name, scenario, mode),
            }
            for name, scenario, mode, question in comparisons
        ],
    }


def verify(config):
    """Refuse source/config drift before the existing runner writes or launches work."""
    plan = json.loads(config.campaign_plan.read_text())
    root = Path(__file__).resolve().parents[2]
    if (
        plan.get("contract") != "usefulness-campaign-v1"
        or plan.get("status") != "planned-not-run"
        or plan.get("model") != run.MODEL
        or plan.get("reasoning_effort") != run.EFFORT
        or plan["runtime_source"]["files_sha256"] != source_identity(root)["files_sha256"]
        or Path(plan["runtime_executable"]).resolve() != (root / ".venv/bin/anaxigraph").resolve()
        or plan["runtime_executable_sha256"]
        != hashlib.sha256((root / ".venv/bin/anaxigraph").read_bytes()).hexdigest()
    ):
        raise ValueError("Campaign source/runtime changed; freeze a new plan before running")
    commands = (
        plan["qualification"]
        + [plan["preflight"], plan["pilot"]]
        + [row["command"] for row in plan["comparisons"]]
    )
    keys = (
        "live",
        "synthetic",
        "track",
        "agent",
        "scenarios",
        "replicates",
        "stages",
        "call_timeout",
        "analysis_workers",
        "control_mode",
        "control_review_calls",
        "random_seed",
        "preflight_only",
        "runtime",
        "runtime_source_root",
        "state",
    )

    def settings(args):
        return {
            key: str(value.resolve()) if isinstance(value := getattr(args, key), Path) else value
            for key in keys
        }

    actual = settings(config)
    if not any(
        actual == settings(run.parser().parse_args(command["argv"][3:])) for command in commands
    ):
        raise ValueError("Invocation differs from the frozen campaign commands")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replicates", type=int, default=3)
    parser.add_argument("--random-seed", type=int, default=20261029)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    if output == root or root in output.parents:
        parser.error("Write the frozen plan outside the source checkout")
    plan = build(
        root,
        args.state_root,
        replicates=args.replicates,
        random_seed=args.random_seed,
        plan_path=output,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    # A changed design gets a new plan; never replace an already preregistered one.
    with output.open("x") as stream:
        json.dump(plan, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"plan": str(output), "status": plan["status"], "model_calls": 0}))


if __name__ == "__main__":
    main()
