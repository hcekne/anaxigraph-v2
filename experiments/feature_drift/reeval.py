"""Re-score committed steps with the current hidden tests, without rerunning any agent.

Use after correcting a hidden test: every step commit is checked out into a temporary
worktree sharing the chain's virtualenv, and its `hidden` result is replaced.

    uv run python experiments/feature_drift/reeval.py --state ~/drift/pilot
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

import runner  # noqa: E402


def reevaluate(chain: Path) -> None:
    path = chain / "steps.jsonl"
    rows = runner.read_jsonl(path)
    repo = chain / "repo"
    for row in rows:
        with tempfile.TemporaryDirectory(prefix="drift-reeval-") as tmp:
            tree = Path(tmp) / "tree"
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "worktree",
                    "add",
                    "--detach",
                    "-q",
                    str(tree),
                    row["commit"],
                ],
                check=True,
            )
            try:
                (tree / ".venv").symlink_to(repo / ".venv")
                files = [f"step_{n:02d}.py" for n in range(1, row["step"] + 1)]
                out = chain / "eval" / f"step-{row['step']:02d}" / "hidden-reeval.xml"
                row["hidden"] = runner.hidden_tests(tree, files, row["step"], out)
            finally:
                subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(tree)])
                shutil.rmtree(tree, ignore_errors=True)
        by_file = row["hidden"]["by_file"]
        print(
            chain.name, row["step"], {k: f"{v['passed']}/{v['total']}" for k, v in by_file.items()}
        )
    path.write_text("".join(json.dumps(row, default=str) + "\n" for row in rows))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--chains", help="comma-separated chain names (default: all)")
    args = parser.parse_args()
    wanted = set(args.chains.split(",")) if args.chains else None
    for chain in sorted(args.state.iterdir()):
        if (chain / "steps.jsonl").exists() and (wanted is None or chain.name in wanted):
            reevaluate(chain)


if __name__ == "__main__":
    main()
