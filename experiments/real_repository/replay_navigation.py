"""Replay merged pull requests at their parent commit: does the map point at the files that
were really changed, and which extra signals beat plain keyword search?

Collects, per pull request, the product's scored suggestions, a BM25 baseline, the import
neighbours of the top suggestions and their co-change history, so ranking variants can be
compared afterwards (compare_navigation.py) without replaying. No model calls.

    git clone --shared /path/to/repository /scratch/clone
    uv run python experiments/real_repository/replay_navigation.py /scratch/clone /scratch/out

The clone is checked out at many commits, so never point this at a working checkout. Pull
requests are recognised by GitHub merge commits from fix/, feat/ or feature/ branches.
"""

import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

os.environ["ANAXIGRAPH_EVIDENCE"] = "off"
from anaxigraph.analysis.charter import relations
from anaxigraph.analysis.retrieval import rank_owners
from anaxigraph.application.core import Application
from anaxigraph.contracts import SOURCE_SUFFIXES
from anaxigraph.indexing.facts import is_test

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cochange import capture_history  # noqa: E402

repo, SC = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else 200
SC.mkdir(parents=True, exist_ok=True)
start = subprocess.run(
    ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
).stdout.strip()


def git(*args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


rows = []
for line in git(
    "log", "--first-parent", "--merges", "-n", "700", "--format=%H%x1f%P%x1f%s%x1f%b%x1e"
).split("\x1e"):
    line = line.strip("\n")
    if not line:
        continue
    sha, parents, subject, body = line.split("\x1f")
    m = re.match(r"Merge pull request #(\d+) from [^/]+/([a-z]+)/(.+)", subject)
    if not m or m.group(2) not in {"fix", "feat", "feature"}:
        continue
    task = (body.strip().splitlines() or [""])[0].strip() or m.group(3).replace("-", " ")
    parent = parents.split()[0]
    changed = [
        r.split("\t")
        for r in git("diff", "--name-status", "--no-renames", parent, sha).splitlines()
    ]
    truth = [
        p for kind, p in changed if kind == "M" and p.endswith(SOURCE_SUFFIXES) and not is_test(p)
    ]
    if 1 <= len(truth) <= 25:
        rows.append(dict(pr=int(m.group(1)), parent=parent, task=task, truth=truth))
rows = rows[:LIMIT]
print(
    f"replaying {len(rows)} fix/feature pull requests that modify 1..25 existing production source files",
    flush=True,
)

TOK = re.compile(r"[a-z][a-z0-9]{2,}")
STOP = set(
    "the and for with from into that this when only keep verify add use support make after before then "
    "their its are not all change improve implement should existing".split()
)


def toks(text):
    return [
        w for w in TOK.findall(re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower()) if w not in STOP
    ]


bm_cache = {}


def bm25(sources, fingerprints, task, k1=1.5, b=0.75):
    docs = {}
    for p, t in sources.items():
        if is_test(p):
            continue
        key = (p, fingerprints[p])
        if key not in bm_cache:
            bm_cache[key] = Counter(toks(p + " " + t))
        docs[p] = bm_cache[key]
    n = len(docs)
    avg = sum(sum(c.values()) for c in docs.values()) / max(n, 1)
    q = set(toks(task))
    df = {w: sum(1 for c in docs.values() if w in c) for w in q}
    scores = {}
    for p, c in docs.items():
        length = sum(c.values())
        s = 0.0
        for w in q:
            if c.get(w):
                s += (
                    math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                    * c[w]
                    * (k1 + 1)
                    / (c[w] + k1 * (1 - b + b * length / avg))
                )
        if s:
            scores[p] = s
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:100]


app = Application(repo, SC / "navigation.sqlite")
out = open(SC / "replay_navigation.jsonl", "w")
started = time.time()
for i, r in enumerate(rows):
    git("checkout", "-q", "--detach", r["parent"])
    graph = app._live()
    sources = app.indexer.captured_sources(graph.snapshot)
    t = time.time()
    ranked = [
        (x["path"], x["score"])
        for x in rank_owners(graph, sources, r["task"], 30, app._terms)
        if not is_test(x["path"])
    ]
    rank_s = time.time() - t
    bm = bm25(sources, graph.snapshot.files, r["task"])
    modules, raw = relations(graph)
    prod = {p for p, n in modules.items() if not n.data.get("is_test")}
    top = [p for p, _ in ranked[:15]] + [p for p, _ in bm[:15]]
    neighbours = {
        p: sorted((raw[p]["dependencies"] | raw[p]["consumers"]) & prod)[:60]
        for p in dict.fromkeys(top)
        if p in raw
    }
    tests_of = {
        p: sorted(t_ for t_ in raw[p]["consumers"] if t_ not in prod)[:20]
        for p in dict.fromkeys(top)
        if p in raw
    }
    history = capture_history(repo, graph.snapshot.revision, set(graph.snapshot.files))
    cochange = {p: Counter() for p in dict.fromkeys(top)}
    for change in history.get("changes", []):
        paths = set(change["paths"])
        if change["size"] > 30:
            continue
        for p in cochange.keys() & paths:
            for other in paths - {p}:
                if other in prod:
                    cochange[p][other] += 1
    out.write(
        json.dumps(
            dict(
                pr=r["pr"],
                task=r["task"],
                truth=r["truth"],
                in_map=sum(p in modules for p in r["truth"]),
                ranked=ranked,
                bm=bm,
                neighbours=neighbours,
                cochange={p: c.most_common(15) for p, c in cochange.items()},
                rank_s=round(rank_s, 3),
                modules=len(modules),
            )
        )
        + "\n"
    )
    out.flush()
    if (i + 1) % 10 == 0:
        print(f"[{i + 1}/{len(rows)}] {time.time() - started:.0f}s", flush=True)
git("checkout", "-q", "--detach", start)
print("done", flush=True)
