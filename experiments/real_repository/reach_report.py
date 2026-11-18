"""How much of the suite does a change need? The reach report.

Test reach is the share of the suite's time that a change needs. This reads one saved test
record and the checkout's history, replays past changes with the product's own selection, and
prints the repository's number, where it comes from, its parts ranked, the files that cost
most, and what the largest changes would do to the number.

    uv run python experiments/real_repository/reach_report.py /scratch/clone --suite backend

The clone must be at the commit the suite was recorded at, with the record saved in it by
`anaxigraph record-tests`. Past changes are merged fix/feature pull requests, or every commit
on the main line with `--history commits`. Nothing is executed and nothing is written to the
clone.

A what-if is a best case, not a promise. `--then` replays named ones in order, each on top of
the last: `attributed` (the recording sees inside the tests that start another program),
`skip:PATTERN` (these files no longer have to change), `local:PATTERN` (their top-level
changes reach only the tests that ran them) and `source-reads` (Python source opened on
behalf of other code no longer counts as data it read). In a pattern `*` stays inside one
folder and `**` does not; several patterns are separated by commas.
"""

from __future__ import annotations

import argparse
import ast
import collections
import contextlib
import json
import posixpath
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from anaxigraph.analysis import test_selection as selection
from anaxigraph.indexing import index
from anaxigraph.indexing.python import _import_fingerprint

FLAT, GROUP = 30, 5  # Files that make a folder flat, and files that make a name group a part.
PULL = re.compile(r"Merge pull request #(\d+) from [^/]+/(fix|feat|feature)/")
ALWAYS = "the recording sees inside the tests that start another program"
Change = tuple[str, str, bool]  # Added or modified, the path, import-time code unchanged.


@contextlib.contextmanager
def remembered_users():
    """The selection works out who uses whom on every call; between replays of one checkout
    that never changes, so it is kept. The graphs are held, so no identity is reused."""
    original, known = selection._users, []

    def users(graph, inside):
        for held, files, found in known:
            if held is graph and files == inside:
                return found
        known.append((graph, set(inside), original(graph, inside)))
        return known[-1][2]

    selection._users = users
    try:
        yield
    finally:
        selection._users = original


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if done.returncode:
        raise subprocess.CalledProcessError(done.returncode, args, done.stdout, done.stderr)
    return done.stdout


def matcher(patterns: str):
    """Paths matching any of the comma-separated patterns."""
    parts = []
    for pattern in filter(None, patterns.split(",")):
        pieces = re.split(r"(\*\*|\*)", pattern.strip())
        parts.append(
            "".join(
                ".*" if piece == "**" else "[^/]*" if piece == "*" else re.escape(piece)
                for piece in pieces
            )
        )
    found = re.compile("|".join(f"(?:{part})" for part in parts) or r"(?!)")
    return lambda path: found.fullmatch(path) is not None


def summary(values: list[float]) -> dict[str, Any]:
    return {
        "median": statistics.median(values),
        "mean": statistics.mean(values),
        "nearly_all": sum(value >= 0.95 for value in values),
        "under_fifth": sum(value < 0.2 for value in values),
    }


class Replay:
    """One record and its checkout, and the selection after a simulated change."""

    def __init__(self, repo: Path, record: dict[str, Any]):
        self.repo, self.record = repo, record
        self.graph = index(repo)
        self.files = self.graph.snapshot.files
        self.tests: list[str] = record["tests"]
        self.seconds = dict(zip(self.tests, record["seconds"]))
        self.total = sum(record["seconds"]) or 1.0
        self.scope = tuple(record["scope"])
        self.whole = set(record["ran_at_import"])  # Compared as a whole, not by import code.
        self.reads = record["reads"] or {}
        base = selection._Selection(record, self.graph)
        self.modules, self.users = base.modules, base.users
        self.sources = sorted(
            path
            for path in self.modules
            if path.startswith(self.scope) and path.endswith(".py") and not self.test_like(path)
        )
        self.part = parts_of(self.sources)
        self.always_rules, self.always_whole = self._always()
        self.always_tests = set().union(*self.always_rules.values())

    def _always(self) -> tuple[dict[str, set[str]], list[str]]:
        """The tests picked for every change, by the rule that picks them, and any reason the
        record gives for picking the whole suite whatever changed."""
        base = selection._Selection(self.record, self.graph)
        found: dict[str, set[str]] = {}
        original = base.add

        def add(path: str, rule: str, tests: set[str]) -> None:
            found.setdefault(rule, set()).update(test for test in tests if test in self.files)
            original(path, rule, tests)

        base.add = add  # type: ignore[method-assign]
        limit = getattr(selection, "_recording_limit", lambda record: None)(self.record)
        selection._unseen(base, [test for test in self.tests if test in self.files])
        return found, [*([limit] if limit else []), *base.everything]

    def share(self, tests: Any) -> float:
        return sum(self.seconds.get(test, 0.0) for test in set(tests)) / self.total

    def numbered(self, numbers: Any) -> set[str]:
        return {self.tests[number] for number in numbers}

    def test_like(self, path: str) -> bool:
        name = posixpath.basename(path)
        stem = name.removesuffix(".py")
        node = self.modules.get(path)
        return bool(
            path in self.seconds
            or name == "conftest.py"
            or stem.startswith("test_")
            or stem.endswith("_test")
            or (node is not None and node.data.get("is_test"))
        )

    def _copy(self, attributed: bool, source_reads: bool) -> dict[str, Any]:
        copied = dict(self.record)
        for key in ("files", "imports", "executed"):
            copied[key] = dict(self.record[key])
        copied["unloaded"] = list(self.record["unloaded"])
        reads = self.record["reads"]
        if reads:
            reads = dict(reads, opened=dict(reads["opened"]))
            for key in ("by_modules", "through"):
                reads[key] = dict(reads[key], opened=dict(reads[key]["opened"]))
            if attributed:
                reads.update(started=[], process_modules=[], unattributed_processes=[])
            if not source_reads:
                kept = reads["through"]["opened"].items()
                reads["through"]["opened"] = {p: by for p, by in kept if not p.endswith(".py")}
            copied["reads"] = reads
        return copied

    def pick(
        self,
        changes: list[Change],
        *,
        attributed: bool = False,
        all_local: bool = False,
        source_reads: bool = True,
        local: frozenset[str] = frozenset(),
        skip: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """The selection as if the record was taken and then these changes happened."""
        copied = self._copy(attributed, source_reads)
        for kind, path, same in changes:
            if path in skip or path not in self.files:
                continue  # Deleted since: nothing to select for it at this checkout.
            if kind == "A":  # Nothing can have run or read a file that was not there yet.
                for held in (copied["files"], copied["imports"], copied["executed"]):
                    held.pop(path, None)
                copied["unloaded"] = [known for known in copied["unloaded"] if known != path]
                reads = copied["reads"]
                for readers in (reads, reads["by_modules"], reads["through"]) if reads else ():
                    readers["opened"].pop(path, None)
                continue
            copied["files"][path] = "changed"
            stays = same or all_local or path in local
            if path.endswith(".py") and path in copied["imports"] and not stays:
                copied["imports"][path] = "changed"
        return selection.select_tests(copied, self.graph)

    def unchanged_at_import(self, parent: str, commit: str, path: str) -> bool:
        """Whether a change left alone what the record compares for a file: all of it when its
        functions ran outside any test, else only what runs at import."""

        def at(revision: str) -> str | None:
            try:
                tree = ast.parse(git(self.repo, "show", f"{revision}:{path}"))
            except (subprocess.CalledProcessError, SyntaxError, ValueError):
                return None
            return ast.dump(tree) if path in self.whole else _import_fingerprint(tree)

        return at(parent) == at(commit)

    def kind(self, change: Change) -> str:
        kind, path, same = change
        if not path.startswith(self.scope):
            return "outside the suite's folders"
        if self.test_like(path) or "/tests/" in path:
            return "test or test helper"
        if not path.endswith(".py"):
            return "data or other file"
        if kind == "A":
            return "new source file"
        return "source, inside functions" if same else "source, top level"

    def needed(self, changes: list[Change]) -> set[str]:
        """Tests that executed or read a changed file of the suite, and changed tests."""
        found: set[str] = set()
        for kind, path, _ in changes:
            if path.startswith(self.scope):
                found |= self.numbered(self.record["executed"].get(path, ()))
                if kind != "A":
                    found |= self.numbered(self.reads.get("opened", {}).get(path, ()))
                if path in self.seconds:
                    found.add(path)
        return found


def parts_of(sources: list[str]) -> dict[str, str]:
    """A part is a folder. In a flat folder of many files, the files that share the first
    word of their name count as a part that has no folder yet."""
    folders: dict[str, list[str]] = collections.defaultdict(list)
    for path in sources:
        folders[posixpath.dirname(path)].append(path)
    found = {}
    for folder, paths in folders.items():
        heads = collections.Counter(posixpath.basename(p).split("_")[0] for p in paths)
        groups = {head for head, count in heads.items() if count >= GROUP}
        groups = groups if len(paths) > FLAT else set()
        for path in paths:
            head = posixpath.basename(path).split("_")[0]
            if head in groups:
                found[path] = f"{folder}/{head}_*"
            else:
                found[path] = f"{folder}/ (other files)" if groups else f"{folder}/"
    return found


def past_changes(replay: Replay, history: str, limit: int) -> list[dict[str, Any]]:
    """Past changes that touched the suite's folders, newest first."""
    rows = []
    form = "--format=%H%x1f%P%x1f%cs%x1f%s%x1e"
    merges = ["--merges"] if history == "pulls" else []
    for line in git(replay.repo, "log", "--first-parent", *merges, form).split("\x1e"):
        if not line.strip("\n"):
            continue
        commit, parents, when, subject = line.strip("\n").split("\x1f")
        pull = PULL.match(subject)
        if not parents or (history == "pulls" and not pull):
            continue
        parent = parents.split()[0]
        listed = git(replay.repo, "diff", "--name-status", "--no-renames", parent, commit)
        named = [row.split("\t") for row in listed.splitlines()]
        if not any(path.startswith(replay.scope) for _, path in named):
            continue
        files = []
        for kind, path in named:
            compared = kind != "A" and path.endswith(".py") and path.startswith(replay.scope)
            same = compared and path in replay.files
            files.append((kind, path, same and replay.unchanged_at_import(parent, commit, path)))
        label = f"#{pull.group(1)}" if history == "pulls" and pull else commit[:9]
        rows.append(
            {"change": label, "when": when, "parent": parent, "commit": commit, "files": files}
        )
        if len(rows) >= limit:
            break
    return rows


def replayed(replay: Replay, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per past change: what is picked today, and with two causes taken away in turn."""
    rows = []
    for change in changes:
        files = change["files"]
        today = replay.pick(files)
        seen = replay.pick(files, attributed=True)
        local = replay.pick(files, attributed=True, all_local=True)
        needed = replay.needed(files)
        rows.append(
            {
                "change": change["change"],
                "when": change["when"],
                "files": sum(path.startswith(replay.scope) for _, path, _ in files),
                "status": today["status"],
                "today": today["time_share"],
                "attributed": seen["time_share"],
                "local": local["time_share"],
                "ran": replay.share(needed),
                "left_out": sorted(needed - set(today["test_files"])),
                "why": today["everything_because"][:1],
            }
        )
    return rows


def file_reach(replay: Replay) -> dict[str, dict[str, Any]]:
    """Per source file: the reach of a change inside a function and of one at the top level,
    beyond the tests picked for every change, and the source files that use it."""
    sources, rows = set(replay.sources), {}
    for path in replay.sources:
        ran = replay.numbered(replay.record["executed"].get(path, ()))
        users = sorted(user for user in replay.users.get(path, ()) if user in sources)
        outside = [user for user in users if replay.part[user] != replay.part[path]]
        row: dict[str, Any] = {
            "part": replay.part[path],
            "tests": len(ran),
            "ran": replay.share(ran),
            "used_by": len(users),
            "used_by_other_parts": sorted(outside),
        }
        if path in replay.record["imports"] or ran:
            inside = replay.pick([("M", path, True)], attributed=True)
            top = replay.pick([("M", path, False)], attributed=True)
            row |= {
                "inside": inside["time_share"],
                "top": top["time_share"],
                "top_whole_suite": top["status"] == "all",
            }
        rows[path] = row
    return rows


def part_rows(files: dict[str, dict[str, Any]], changes: list[dict[str, Any]]) -> list[dict]:
    members: dict[str, list[str]] = collections.defaultdict(list)
    for path, row in files.items():
        members[row["part"]].append(path)
    touched: collections.Counter[str] = collections.Counter()
    for change in changes:
        touched.update({files[path]["part"] for _, path, _ in change["files"] if path in files})
    rows = []
    for part, paths in members.items():
        measured = [path for path in paths if "inside" in files[path]]
        users = {user for path in paths for user in files[path]["used_by_other_parts"]}
        rows.append(
            {
                "part": part,
                "files": len(paths),
                "measured": len(measured),
                "changes": touched[part],
                "inside": statistics.median(files[p]["inside"] for p in measured)
                if measured
                else None,
                "top": statistics.median(files[p]["top"] for p in measured) if measured else None,
                "used_from_outside": sum(bool(files[p]["used_by_other_parts"]) for p in paths),
                "outside_users": len(users),
            }
        )
    return sorted(rows, key=lambda row: (-(row["top"] or 0), -(row["inside"] or 0), row["part"]))


def costly_files(replay: Replay, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Changed files by what they pulled in, summed over the changes that touched them, and
    counted beyond the tests picked for every change."""
    alone: dict[Change, dict[str, Any]] = {}
    rows: dict[str, dict[str, Any]] = {}
    for change in changes:
        for item in change["files"]:
            path = item[1]
            if path not in replay.files or replay.kind(item) == "test or test helper":
                continue
            if item not in alone:
                alone[item] = replay.pick([item], attributed=True)
            picked = alone[item]
            row = rows.setdefault(
                path, {"path": path, "changes": 0, "top_level": 0, "whole_suite": 0, "total": 0.0}
            )
            row["changes"] += 1
            row["top_level"] += replay.kind(item) == "source, top level"
            row["whole_suite"] += picked["status"] == "all"
            row["total"] += picked["time_share"] or 0.0
    return sorted(rows.values(), key=lambda row: (-row["total"], row["path"]))


class WhatIfs:
    """Past changes replayed again under one assumption after another."""

    START: dict[str, Any] = {
        "attributed": False,
        "source_reads": True,
        "all_local": False,
        "local": frozenset(),
        "skip": frozenset(),
    }

    def __init__(self, replay: Replay, changes: list[dict[str, Any]], today: list[float]):
        self.replay, self.changes, self.today = replay, [c["files"] for c in changes], today

    def apply(self, state: dict, values: list[float], kind: str, value: Any) -> tuple[dict, list]:
        after = dict(state)
        affected: Any = range(len(self.changes))
        if kind in ("skip", "local"):
            after[kind] = state[kind] | value
            affected = [
                number
                for number, files in enumerate(self.changes)
                if any(path in value for _, path, _ in files)
            ]
        else:
            after[kind] = value
        values = list(values)
        for number in affected:
            values[number] = self.replay.pick(self.changes[number], **after)["time_share"]
        return after, values

    def alone(self, candidates: list[tuple[str, str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for label, kind, value in candidates:
            _, values = self.apply(self.START, self.today, kind, value)
            rows.append({"change": label, "kind": kind, "value": value} | summary(values))
        return sorted(rows, key=lambda row: row["mean"])

    def in_order(self, candidates: list[tuple[str, str, Any]]) -> list[dict[str, Any]]:
        state, values = self.START, self.today
        rows = [{"change": "today"} | summary(values)]
        for label, kind, value in candidates:
            state, values = self.apply(state, values, kind, value)
            rows.append({"change": label} | summary(values))
        return rows

    def best(self, candidates: list[tuple[str, str, Any]], rounds: int) -> list[dict[str, Any]]:
        """The sequence that lowers the mean most at each step."""
        state, values, left = self.START, self.today, list(candidates)
        rows = [{"change": "today"} | summary(values)]
        for _ in range(min(rounds, len(left))):
            tried = [(candidate, *self.apply(state, values, *candidate[1:])) for candidate in left]
            candidate, state, after = min(tried, key=lambda item: statistics.mean(item[2]))
            if statistics.mean(after) >= statistics.mean(values) - 0.001:
                break
            values = after
            left.remove(candidate)
            rows.append({"change": candidate[0]} | summary(values))
        return rows


def candidates(
    replay: Replay, costly: list[dict], parts: list[dict], files: dict, changes: int
) -> list[tuple[str, str, Any]]:
    """What-ifs worth replaying. Not having to change a file is offered only for a hub: one
    that changed often and needed half the suite or more each time, so that what it holds
    could live with the parts that use it."""
    found: list[tuple[str, str, Any]] = []
    if replay.always_tests:
        found.append((ALWAYS, "attributed", True))
    often = max(2, changes // 20)
    for row in costly[:10]:
        path = frozenset({row["path"]})
        if row["changes"] >= often and row["total"] >= row["changes"] / 2:
            found.append((f"{row['path']} no longer has to change", "skip", path))
        if row["top_level"]:
            found.append((f"top-level changes to {row['path']} stay local", "local", path))
    for part in parts:
        if part["changes"]:
            inside = frozenset(path for path, row in files.items() if row["part"] == part["part"])
            found.append((f"top-level changes in {part['part']} stay local", "local", inside))
    return found


def named(replay: Replay, spec: str) -> tuple[str, str, Any]:
    kind, _, patterns = spec.partition(":")
    if kind == "attributed":
        return ALWAYS, "attributed", True
    if kind == "source-reads":
        return "source opened on behalf of other code is not data it read", "source_reads", False
    if kind not in ("skip", "local") or not patterns:
        raise SystemExit(f"Unknown what-if: {spec}")
    matches = matcher(patterns)
    paths = frozenset(path for path in replay.files if matches(path))
    label = "no longer have to change" if kind == "skip" else "top-level changes stay local"
    return f"{patterns} ({len(paths)} files): {label}", kind, paths


def report(repo: Path, record: dict, history: str, limit: int, then: list[str]) -> dict[str, Any]:
    replay = Replay(repo, record)
    if record.get("revision") != replay.graph.snapshot.revision:
        raise SystemExit("The clone is not at the commit the suite was recorded at")
    unchanged = replay.pick([])
    if unchanged["status"] != "nothing_to_run":
        raise SystemExit(f"The clone differs from the record: {unchanged['everything_because']}")
    changes = past_changes(replay, history, limit)
    if not changes:
        raise SystemExit("No past change touched the suite's folders; try --history commits")
    rows = replayed(replay, changes)
    files = file_reach(replay)
    parts = part_rows(files, changes)
    costly = costly_files(replay, changes)
    today = [row["today"] for row in rows]
    what_ifs = WhatIfs(replay, changes, today)
    offered = candidates(replay, costly, parts, files, len(changes))
    alone = what_ifs.alone(offered + [("every top-level change stays local", "all_local", True)])
    strongest = [(row["change"], row["kind"], row["value"]) for row in alone[:12]]
    strongest = [candidate for candidate in strongest if candidate[1] != "all_local"]
    kinds = collections.Counter(replay.kind(item) for change in changes for item in change["files"])
    measured = [row for row in files.values() if "inside" in row]
    return {
        "suite": record["suite"],
        "revision": record.get("revision"),
        "provenance": record.get("provenance", {}).get("status", "unverified"),
        "test_files": len(replay.tests),
        "suite_minutes": replay.total / 60,
        "history": history,
        "changes": len(rows),
        "first": rows[-1]["when"],
        "last": rows[0]["when"],
        "number": summary(today)
        | {
            "whole_suite": sum(row["status"] == "all" for row in rows),
            "left_out": sum(bool(row["left_out"]) for row in rows),
        },
        "layers": {
            "ran": statistics.mean(row["ran"] for row in rows),
            "careful": statistics.mean(row["local"] - row["ran"] for row in rows),
            "top_level": statistics.mean(row["attributed"] - row["local"] for row in rows),
            "always": statistics.mean(row["today"] - row["attributed"] for row in rows),
        },
        "always": {
            "share": replay.share(replay.always_tests),
            "test_files": len(replay.always_tests),
            "rules": {rule: replay.share(tests) for rule, tests in replay.always_rules.items()},
            "whole_suite_because": replay.always_whole[:5],
        },
        "kinds": dict(kinds.most_common()),
        "typical_file": {
            "files": len(measured),
            "inside": statistics.median(row["inside"] for row in measured) if measured else None,
            "top": statistics.median(row["top"] for row in measured) if measured else None,
        },
        "parts": parts,
        "costly": costly[:40],
        "alone": [{k: v for k, v in row.items() if k != "value"} for row in alone],
        "best": what_ifs.best(strongest, 5),
        "then": what_ifs.in_order([named(replay, spec) for spec in then]) if then else [],
        "pull_requests": rows,
        "files": files,
    }


def percent(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:5.0%}"


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    widths = [max(len(str(row[i])) for row in [header, *rows]) for i in range(len(header))]
    lines = []
    for row in [header, ["-" * width for width in widths], *rows]:
        cells = [str(cell).ljust(width) for cell, width in zip(row, widths)]
        lines.append("  " + "  ".join(cells).rstrip())
    return lines


def render(found: dict[str, Any]) -> str:
    number, layers, always = found["number"], found["layers"], found["always"]
    what = "merged pull requests" if found["history"] == "pulls" else "commits"
    lines = [
        f"Reach report: suite {found['suite']}, {found['test_files']} test files, "
        f"{found['suite_minutes']:.0f} minutes of tests, record {found['provenance']}",
        "",
        "1. The number",
        f"  The typical change needs {number['median']:.0%} of the suite (mean {number['mean']:.0%}),",
        f"  over {found['changes']} {what} from {found['first']} to {found['last']}.",
        f"  {number['nearly_all']} need 95% or more, {number['under_fifth']} need under 20%, "
        f"{number['whole_suite']} need the whole suite by rule.",
        f"  Changes where a needed test was left out: {number['left_out']}.",
        "",
        "2. Where the mean comes from",
        f"  {percent(layers['ran'])}  tests that executed or read what the change touched",
        f"  {percent(layers['careful'])}  careful rules: new files, test helpers, files read as data",
        f"  {percent(layers['top_level'])}  top-level changes, which pick the tests of every file "
        "that uses the file",
        f"  {percent(layers['always'])}  tests picked for every change "
        f"({always['test_files']} test files, {always['share']:.0%} of suite time)",
    ]
    for rule, share in sorted(always["rules"].items(), key=lambda item: -item[1]):
        lines.append(f"           of those, {rule}: {share:.0%} of suite time")
    for reason in always["whole_suite_because"]:
        lines.append(f"  Every change picks the whole suite: {reason}")
    lines += ["", "3. What the changes touched"]
    lines += table(["Kind of file", "Files"], [[k, str(n)] for k, n in found["kinds"].items()])
    typical = found["typical_file"]
    lines += [
        "",
        "4. Parts, by the reach of their typical file (beyond the tests picked for every change)",
        f"  The typical source file: {percent(typical['inside']).strip()} for a change inside a "
        f"function, {percent(typical['top']).strip()} for one at the top level "
        f"({typical['files']} files measured).",
    ]
    shown = [row for row in found["parts"] if row["measured"] >= 3][:16]
    lines += table(
        ["Part", "Files", "Changes", "Inside a function", "Top level", "Used from outside"],
        [
            [
                row["part"],
                str(row["files"]),
                str(row["changes"]),
                percent(row["inside"]).strip(),
                percent(row["top"]).strip(),
                f"{row['used_from_outside']} files, by {row['outside_users']}",
            ]
            for row in shown
        ],
    )
    lines += ["", "5. Files that cost most: how often changed, times what each change pulls in"]
    lines += table(
        ["File", "Changed in", "Each time", "Whole suite", "Suite runs in total"],
        [
            [
                row["path"],
                str(row["changes"]),
                percent(row["total"] / row["changes"]).strip(),
                str(row["whole_suite"]),
                f"{row['total']:.1f}",
            ]
            for row in found["costly"][:12]
        ],
    )
    lines += ["", "6. What would lower it (best cases, each replayed)"]
    for title, rows in (
        ("One at a time:", found["alone"][:8]),
        ("The strongest sequence, each on top of the last:", found["best"]),
        ("As asked, each on top of the last:", found["then"]),
    ):
        if rows:
            lines.append(f"  {title}")
            lines += table(
                ["Change", "Typical", "Mean", "Under 20%"],
                [
                    [
                        row["change"],
                        percent(row["median"]).strip(),
                        percent(row["mean"]).strip(),
                        str(row["under_fifth"]),
                    ]
                    for row in rows
                ],
            )
    return "\n".join(lines)


def main(arguments: list[str] | None = None) -> dict[str, Any]:
    options = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    options.add_argument("clone", type=Path)
    options.add_argument("--suite", required=True)
    options.add_argument("--record", type=Path, help="The saved record, if not in the clone")
    options.add_argument("--history", choices=("pulls", "commits"), default="pulls")
    options.add_argument("--limit", type=int, default=70, help="Past changes to replay")
    options.add_argument("--then", action="append", default=[], help="A what-if, in order")
    options.add_argument("--json", type=Path, help="Write every row here")
    args = options.parse_args(arguments)
    repo = args.clone.resolve()
    saved = args.record or repo / ".anaxigraph-v2" / "test-records" / f"{args.suite}.json"
    started = time.monotonic()
    with remembered_users():
        found = report(repo, json.loads(saved.read_text()), args.history, args.limit, args.then)
    found["seconds"] = round(time.monotonic() - started, 1)
    print(render(found))
    print(f"\n{found['seconds']:.0f} seconds", file=sys.stderr)
    if args.json:
        args.json.write_text(json.dumps(found, indent=1, default=sorted))
    return found


if __name__ == "__main__":
    main()
