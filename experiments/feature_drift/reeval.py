"""Re-score committed steps and probes with the current harness, without rerunning any agent.

Use after correcting a hidden test or a metric: every step commit is checked out into a
temporary worktree sharing the chain's virtualenv, and the chosen results are replaced.
Probe repositories are re-scored in place.

    uv run python experiments/feature_drift/reeval.py --state ~/drift/pilot
    uv run python experiments/feature_drift/reeval.py --state ~/drift/full --chains charter-r0 \\
        --measures own,ci
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics  # noqa: E402
import runner  # noqa: E402

MEASURES = ("hidden", "own", "ci", "shape")


def rescore_step(row: dict, tree: Path, out: Path, measures: set[str], mutants: int) -> None:
    if "hidden" in measures:
        files = [f"step_{n:02d}.py" for n in range(1, row["step"] + 1)]
        row["hidden"] = runner.hidden_tests(tree, files, row["step"], out / "hidden-reeval.xml")
    if "own" in measures:
        row["own_suite"] = runner.own_suite(tree, out / "own-reeval.xml")
    if "shape" in measures:
        row["static"] = metrics.static_metrics(tree)
        row["diff"] = metrics.diff_stats(tree, row["commit"] + "^", row["commit"])
    if "ci" in measures and "ci" in row:
        ci = metrics.ci_quality(tree, seed=row["step"], mutants=mutants)
        runner.write_json(out / "ci-reeval.json", ci)
        row["ci"] = {k: v for k, v in ci.items() if k != "per_file"}


def reevaluate(chain: Path, measures: set[str], mutants: int) -> None:
    path = chain / "steps.jsonl"
    rows = runner.read_jsonl(path)
    repo = chain / "repo"
    for row in rows:
        with tempfile.TemporaryDirectory(prefix="drift-reeval-") as tmp:
            tree = Path(tmp) / "tree"
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(tree), row["commit"]],
                check=True,
            )  # fmt: skip
            try:
                (tree / ".venv").symlink_to(repo / ".venv")
                out = chain / "eval" / f"step-{row['step']:02d}"
                rescore_step(row, tree, out, measures, mutants)
            finally:
                subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(tree)])
                shutil.rmtree(tree, ignore_errors=True)
        hidden = {k: f"{v['passed']}/{v['total']}" for k, v in row["hidden"]["by_file"].items()}
        own = row["own_suite"]
        print(chain.name, row["step"], hidden, f"own {own['passed']}/{own['tests']}",
              (row.get("ci") or {}).get("mean_selected_fraction"), flush=True)  # fmt: skip
    path.write_text("".join(json.dumps(row, default=str) + "\n" for row in rows))

    probes_path = chain / "probes.jsonl"
    probes = runner.read_jsonl(probes_path)
    for row in probes:
        probe_repo = chain / "probes" / row["probe"] / "repo"
        out = chain / "eval" / f"probe-{row['probe']}"
        if "hidden" in measures:
            number = int(row["probe"][1:].split("-")[0])
            steps = [f"step_{n:02d}.py" for n in range(1, 21)]
            row["hidden_probe"] = runner.hidden_tests(
                probe_repo, [f"probe_{number}.py"], 20, out / "probe-reeval.xml"
            )
            row["hidden_regression"] = runner.hidden_tests(
                probe_repo, steps, 20, out / "steps-reeval.xml"
            )
        if "own" in measures:
            row["own_suite"] = runner.own_suite(probe_repo, out / "own-reeval.xml")
        if "ci" in measures:
            row["ci_selected"], row["ci_total"] = runner.selection_size(
                probe_repo, row["diff"]["changed_files"]
            )
        print(chain.name, row["probe"], row["own_suite"], row["ci_selected"], row["ci_total"])
    if probes:
        probes_path.write_text("".join(json.dumps(r, default=str) + "\n" for r in probes))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--chains", help="comma-separated chain names (default: all)")
    parser.add_argument(
        "--measures", default="hidden", help=f"comma-separated: {','.join(MEASURES)}"
    )
    parser.add_argument("--mutants", type=int, default=10)
    args = parser.parse_args()
    measures = set(args.measures.split(","))
    if not measures <= set(MEASURES):
        parser.error(f"unknown measures: {measures - set(MEASURES)}")
    wanted = set(args.chains.split(",")) if args.chains else None
    for chain in sorted(args.state.iterdir()):
        if (chain / "steps.jsonl").exists() and (wanted is None or chain.name in wanted):
            reevaluate(chain, measures, args.mutants)


if __name__ == "__main__":
    main()
