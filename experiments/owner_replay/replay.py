"""Replay historical commits against AnaxiGraph and grep baselines.

For each selected commit, check out its parent in a scratch clone, give the commit message to
`prepare_change` and compare its suggestions with the files the commit actually changed.
The scratch clone walks forward oldest-first so that `--mode mapped` keeps annotations current
for each parent by incremental mapping, as a maintained record would in normal use.

    uv run python experiments/owner_replay/replay.py --source ~/repos/target \
        --workdir /tmp/replay --commits 20 --mode mapped

Results append to `<workdir>/results-<mode>.jsonl`; rerunning resumes after completed commits.
Mapped mode calls the configured model providers and therefore costs money.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scoring  # noqa: E402


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def select_commits(source: Path, count: int, max_files: int, scan_depth: int = 600) -> list[dict]:
    """Recent non-merge commits changing 1..max_files production Python files, oldest first."""
    selected = []
    for sha in git(source, "log", "--no-merges", "--format=%H", f"-{scan_depth}").split():
        churn = {}
        for line in git(source, "show", "--numstat", "--format=", sha).splitlines():
            added, deleted, path = line.split("\t", 2)
            churn[path] = (int(added) if added.isdigit() else 0) + (
                int(deleted) if deleted.isdigit() else 0
            )
        production = [p for p in churn if scoring.is_python(p) and not scoring.is_test(p)]
        if 1 <= len(production) <= max_files:
            message = git(source, "log", "-1", "--format=%s%n%n%b", sha).strip()
            selected.append({"sha": sha, "message": message[:1500], "churn": churn})
        if len(selected) >= count:
            break
    return selected[::-1]


def truth(commit: dict, files: dict[str, str]) -> dict:
    churn = commit["churn"]
    prod = {p for p in churn if scoring.is_python(p) and not scoring.is_test(p)}
    tests = {p for p in churn if scoring.is_python(p) and scoring.is_test(p)}
    existing_prod = prod & set(files)
    primary = max(sorted(existing_prod), key=lambda p: churn[p]) if existing_prod else None
    return {
        "changed_prod": sorted(existing_prod),
        "changed_tests": sorted(tests & set(files)),
        "new_python_files": sorted((prod | tests) - set(files)),
        "primary": primary,
    }


def _brief(app, task: str, *, capped: bool) -> dict:
    """The delivered brief, or the same brief before the default size cap trims its lists."""
    from anaxigraph.analysis import guidance

    if capped:
        return app.prepare_change(task)
    original = guidance._fit_brief
    guidance._fit_brief = lambda brief: brief
    try:
        return app.prepare_change(task)
    finally:
        guidance._fit_brief = original


def evaluate(app, graph, commit: dict, files: dict[str, str], label: str) -> dict:
    from anaxigraph.analysis.guidance import affected_paths

    facts = truth(commit, files)
    expected = set(facts["changed_prod"])
    modules = {n.path for n in graph.nodes if n.kind == "module"}
    production = {p for p in modules if not scoring.is_test(p)}
    row = {
        f"{label}_annotated_modules": sum(
            1 for n in graph.nodes if n.kind == "module" and n.data.get("annotation")
        )
    }
    for variant, capped in ((label, True), (f"{label}_uncapped", False)):
        brief = _brief(app, commit["message"], capped=capped)
        owners = [o["path"] for o in brief["likely_owners"]]
        affected = affected_paths(graph, set(owners)) & modules
        row |= {
            f"{variant}_owners": owners,
            f"{variant}_owner_rank": scoring.rank_of_first_hit(owners, expected),
            f"task_{variant}_affected_recall": scoring.recall(affected, expected),
            f"task_{variant}_affected_prod_size": len(affected & production),
            f"task_{variant}_test_recall": scoring.recall(affected, set(facts["changed_tests"])),
            f"task_{variant}_test_size": len(affected - production),
            f"{variant}_brief_bytes": len(json.dumps(brief, ensure_ascii=False, indent=2)),
        }
    return row


def history(source: Path, sha: str, depth: int = 1000) -> list[set[str]]:
    """Python files changed together in earlier commits, newest first."""
    log = git(
        source, "log", "--no-merges", "--name-only", "--format=format:@@", f"-{depth}", f"{sha}^"
    )
    return [
        {line for line in block.splitlines() if scoring.is_python(line)}
        for block in log.split("@@")
        if block.strip()
    ]


def oracle(graph, commit: dict, files: dict[str, str], past: list[set[str]]) -> dict:
    """Given the true primary file, how well do the map and grep find co-changed files/tests?"""
    from anaxigraph.analysis.guidance import affected_paths

    facts = truth(commit, files)
    primary = facts["primary"]
    if primary is None:
        return {}
    others = set(facts["changed_prod"]) - {primary}
    tests = set(facts["changed_tests"])
    modules = {n.path for n in graph.nodes if n.kind == "module"}
    mapped = (affected_paths(graph, {primary}) & modules) - {primary}
    grepped = scoring.grep_importers(files, {primary}) - {primary}
    grep_tests = scoring.grep_tests(files, {primary})
    production = lambda paths: {p for p in paths if not scoring.is_test(p)}  # noqa: E731
    # Give history the same list length as AnaxiGraph so precision stays comparable.
    budget = max(3, len(production(mapped)))
    test_budget = max(3, len(mapped - production(mapped)))
    coupled_prod = scoring.cochanged(
        [{p for p in c if p in files and not scoring.is_test(p)} for c in past], primary, budget
    )
    coupled_tests = scoring.cochanged(
        [{p for p in c if p in files and (p == primary or scoring.is_test(p))} for c in past],
        primary,
        test_budget,
    )
    return {
        "oracle_consumer_recall_history": scoring.recall(coupled_prod, others),
        "oracle_test_recall_history": scoring.recall(coupled_tests, tests),
        "oracle_consumer_size_history": len(coupled_prod),
        "oracle_test_size_history": len(coupled_tests),
        "oracle_consumer_recall_anaxigraph": scoring.recall(mapped, others),
        "oracle_consumer_recall_grep": scoring.recall(grepped, others),
        "oracle_consumer_size_anaxigraph": len(production(mapped)),
        "oracle_consumer_size_grep": len(production(grepped)),
        "oracle_test_recall_anaxigraph": scoring.recall(mapped, tests),
        "oracle_test_recall_grep": scoring.recall(grep_tests, tests),
        "oracle_test_size_anaxigraph": len(mapped - production(mapped)),
        "oracle_test_size_grep": len(grep_tests),
    }


def run(args: argparse.Namespace) -> None:
    from anaxigraph.application.core import Application

    source, workdir = args.source.resolve(), args.workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    clone = workdir / "clone"
    if not clone.exists():
        subprocess.run(["git", "clone", "--quiet", str(source), str(clone)], check=True)
    results = workdir / f"results-{args.mode}.jsonl"
    done = (
        {json.loads(line)["commit"] for line in results.read_text().splitlines()}
        if results.exists()
        else set()
    )
    config = json.loads(args.config.read_text()) if args.config else None
    commits = select_commits(source, args.commits, args.max_files)
    for commit in commits:
        if commit["sha"] in done:
            continue
        git(clone, "checkout", "--quiet", "--force", "--detach", f"{commit['sha']}^")
        record = clone / "anaxigraph" / "record.json"
        record.unlink(missing_ok=True)
        files = scoring.read_python(clone)
        prod_files = {p: t for p, t in files.items() if not scoring.is_test(p)}

        structural = Application(clone, workdir / "structural.sqlite")
        structural.scan()
        graph = structural._current()
        row = {
            "commit": commit["sha"][:12],
            "subject": commit["message"].splitlines()[0][:100],
            **truth(commit, files),
            **evaluate(structural, graph, commit, files, "structural"),
            **oracle(graph, commit, files, history(source, commit["sha"])),
        }
        grep = scoring.grep_owners(prod_files, commit["message"], 3)
        row["grep_owners"] = grep
        row["grep_owner_rank"] = scoring.rank_of_first_hit(grep, set(row["changed_prod"]))

        if args.mode == "mapped":
            mapped = Application(clone, workdir / "mapped.sqlite")
            mapped.scan()
            started = time.time()
            status = mapped.workflow_start("mapping", config, None, True)
            record.unlink(missing_ok=True)
            run_summary = status.get("items", [status])[0] if "items" in status else status
            row["mapping"] = {
                "seconds": round(time.time() - started, 1),
                "status": run_summary.get("status"),
                "model_calls": sum(
                    s.get("model_calls", 0) for s in run_summary.get("stage_timings", [])
                ),
                "failed_batches": run_summary.get("failed_batches"),
                "failures": [
                    str(item)[:300]
                    for item in (run_summary.get("attempt_failures") or [])[:3]
                    + (run_summary.get("failures") or [])[:3]
                ],
            }
            row.update(evaluate(mapped, mapped._current(), commit, files, "mapped"))
        with results.open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        print(
            f"{row['commit']} mapped={row.get('mapped_owner_rank')} "
            f"structural={row['structural_owner_rank']} grep={row['grep_owner_rank']} "
            f"{row['subject'][:60]}",
            flush=True,
        )
    rows = [json.loads(line) for line in results.read_text().splitlines()]
    summary = scoring.summarize(rows)
    (workdir / f"summary-{args.mode}.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True, help="Target Git repository")
    parser.add_argument("--workdir", type=Path, required=True, help="Scratch directory")
    parser.add_argument("--commits", type=int, default=20)
    parser.add_argument("--max-files", type=int, default=8)
    parser.add_argument("--mode", choices=("structural", "mapped"), default="structural")
    parser.add_argument("--config", type=Path, help="Optional mapping workflow config JSON")
    args = parser.parse_args()
    os.environ.setdefault("ANAXIGRAPH_EVIDENCE", "off")
    run(args)


if __name__ == "__main__":
    main()
