"""Replay merged pull requests against one recorded run: which tests would be picked?

Uses the product's own record reader and selection. Each pull request is simulated as "the
record was taken, then this change happened": its changed files are made to differ from the
record, with import-time code marked changed only where the pull request really changed it.

    uv run python experiments/real_repository/replay_tests.py /scratch/clone \\
        --coverage /scratch/run/.coverage --junit /scratch/run/junit.xml \\
        --reads /scratch/run/.anaxigraph-reads.json \\
        --suite backend --scope backend/ --run-root backend --recorded-root /path/of/recording

The clone must be at the commit the suite was recorded at. Nothing is executed.
"""

import collections
import copy
import json
import os
import re
import statistics
import subprocess
from pathlib import Path

os.environ["ANAXIGRAPH_EVIDENCE"] = "off"
import argparse
import ast

from anaxigraph.analysis.test_selection import select_tests
from anaxigraph.indexing import index
from anaxigraph.indexing.python import _import_fingerprint
from anaxigraph.indexing.test_record import record_from_coverage

options = argparse.ArgumentParser(description=__doc__.splitlines()[0])
options.add_argument("clone", type=Path)
options.add_argument("--coverage", type=Path, required=True)
options.add_argument("--junit", type=Path, nargs="+")
options.add_argument("--reads", type=Path, help="Reads recorder report of the same run")
options.add_argument("--suite", required=True)
options.add_argument("--scope", nargs="+", required=True)
options.add_argument("--run-root", default=".")
options.add_argument("--recorded-root")
options.add_argument("--limit", type=int, default=200)
options.add_argument("--json", type=Path, help="Write the per-pull-request rows here")
args = options.parse_args()
repo, LIMIT = args.clone.resolve(), args.limit
SCOPE = tuple(item.rstrip("/") + "/" for item in args.scope)
graph = index(repo)
record = record_from_coverage(
    graph,
    args.coverage,
    suite=args.suite,
    scope=args.scope,
    run_root=args.run_root,
    recorded_root=args.recorded_root or str(repo),
    junit=args.junit,
    reads=args.reads,
)
READS = record["reads"]
print(
    f"record: {len(record['tests'])} test files, {len(record['executed'])} executed sources, {len(record['imports'])} loaded modules ({len(record['ran_at_import'])} with functions that ran outside any test), {len(record['unloaded'])} known not to be loaded, {len(record['files'])} files compared, {len(json.dumps(record)) / 1e6:.2f} MB"
)
if READS:
    print(
        f"reads: {len(READS['opened'])} files read by tests, {len(READS['by_modules']['opened'])} read while a module was imported, {len(READS['listed'])} folders scanned, {len(READS['started'])} test files started a process"
    )
print(
    "unchanged checkout:",
    {k: v for k, v in select_tests(record, graph).items() if k in ("status", "selected", "total")},
)
total_seconds = sum(record["seconds"])
seconds = dict(zip(record["tests"], record["seconds"]))


def share(numbers):
    return sum(seconds[record["tests"][i]] for i in set(numbers)) / total_seconds


seen = {i for found in record["executed"].values() for i in found}
for kind in ("opened", "listed"):
    seen |= {i for found in (READS or {}).get(kind, {}).values() for i in found}
unseen = set(range(len(record["tests"]))) - seen
print(f"tests with no recorded input: {len(unseen)} files, {share(unseen):.1%} of suite time")
if READS:
    print(
        f"tests that started another process: {len(READS['started'])} files, {share(READS['started']):.1%} of suite time; together with the above {share(unseen | set(READS['started'])):.1%}, selected for every change"
    )


def git(*args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def fingerprint_at(commit, path):
    """What the record compares for a file: everything when its functions ran outside any
    test, else what runs at import."""
    try:
        tree = ast.parse(git("show", f"{commit}:{path}"))
    except (subprocess.CalledProcessError, SyntaxError, ValueError):
        return None
    if path in record["ran_at_import"]:
        return ast.dump(tree, include_attributes=False)
    return _import_fingerprint(tree)


rows = []
merges = git("log", "--first-parent", "--merges", "-n", "600", "--format=%H%x1f%P%x1f%s%x1e").split(
    "\x1e"
)
for line in merges:
    line = line.strip("\n")
    if not line:
        continue
    sha, parents, subject = line.split("\x1f")
    m = re.match(r"Merge pull request #(\d+) from [^/]+/([a-z]+)/", subject)
    if not m or m.group(2) not in {"fix", "feat", "feature"}:
        continue
    parent = parents.split()[0]
    changes = [
        r.split("\t")
        for r in git("diff", "--name-status", "--no-renames", parent, sha).splitlines()
    ]
    scoped = [(kind, path) for kind, path in changes if path.startswith(SCOPE)]
    if not scoped:
        continue
    simulated = copy.deepcopy(record)
    body_only = 0
    # With reads, a file anywhere in the checkout can be an input of this suite's tests.
    for kind, path in changes if READS else scoped:
        if path not in graph.snapshot.files:
            continue  # Deleted since; nothing to select for it at this checkout.
        if kind == "A":
            simulated["files"].pop(path, None)
            simulated["imports"].pop(path, None)
            simulated["executed"].pop(path, None)
            simulated["unloaded"] = [known for known in simulated["unloaded"] if known != path]
            if READS:  # Nothing can have read a file that was not there yet.
                for readers in (simulated["reads"], *simulated["reads"].values()):
                    if isinstance(readers, dict):
                        readers.get("opened", {}).pop(path, None)
            continue
        simulated["files"][path] = "changed-by-this-pull-request"
        if path.endswith(".py") and path in simulated["imports"]:
            same = fingerprint_at(parent, path) == fingerprint_at(sha, path)
            body_only += same
            if not same:
                simulated["imports"][path] = "import-time-code-changed"
    picked = select_tests(simulated, graph)
    executed_only = set()
    for kind, path in scoped:
        executed_only |= {record["tests"][i] for i in record["executed"].get(path, ())}
        if READS and kind != "A":
            executed_only |= {record["tests"][i] for i in READS["opened"].get(path, ())}
    changed_tests = {path for kind, path in scoped if path in set(record["tests"])}
    needed = executed_only | changed_tests
    rows.append(
        dict(
            pr=int(m.group(1)),
            files=len(scoped),
            status=picked["status"],
            share=picked["selected"] / picked["total"],
            time=picked["time_share"],
            floor=sum(seconds.get(t, 0) for t in needed) / total_seconds,
            missed=len(needed - set(picked["test_files"])),
            why=picked["everything_because"][:2],
            body_only=body_only,
            rules=[(r["rule"][:28], r["tests"]) for r in picked["because"][:3]],
        )
    )
    if len(rows) >= LIMIT:
        break

print(f"\nfix/feature pull requests touching the suite's scope: {len(rows)}")


def dist(values):
    v = sorted(values)
    n = len(v)
    return f"median {statistics.median(v):.0%}, p25 {v[n // 4]:.0%}, p75 {v[(3 * n) // 4]:.0%}, mean {statistics.mean(v):.0%}"


print("picked, share of suite time:        ", dist([r["time"] for r in rows]))
print("picked, share of test files:        ", dist([r["share"] for r in rows]))
print(
    "executed-or-changed tests only:     ",
    dist([r["floor"] for r in rows]),
    "(what a rule without any broadening would pick)",
)
print(
    "whole suite selected:",
    sum(r["status"] == "all" for r in rows),
    "| under 20% of time:",
    sum(r["time"] < 0.2 for r in rows),
    "| 20-50%:",
    sum(0.2 <= r["time"] < 0.5 for r in rows),
    "| 50-99%:",
    sum(0.5 <= r["time"] < 0.995 and r["status"] != "all" for r in rows),
)
print(
    "pull requests where a test that executed or read a changed file, or a changed test, was left out:",
    sum(r["missed"] > 0 for r in rows),
)
reasons = collections.Counter()
for r in rows:
    for why in r["why"][:1]:
        reasons[
            why.split(": ", 1)[1][:60]
            + " | "
            + (
                "migration"
                if "/migrations/" in why
                else Path(why.split(":")[0]).suffix or Path(why.split(":")[0]).name
            )
        ] += 1
print("first reason the whole suite was selected:")
for reason, count in reasons.most_common(12):
    print(f"   {count:3d}  {reason}")
if args.json:
    args.json.write_text(json.dumps(rows))
