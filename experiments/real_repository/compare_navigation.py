"""Compare ranking variants on the replayed pull requests. A variant is chosen on the first
half and judged on the second, so a setting cannot win by fitting the pull requests it is
judged on.

    uv run python experiments/real_repository/compare_navigation.py /scratch/out
"""

import itertools
import json
import statistics
import sys
from pathlib import Path

rows = [json.loads(line) for line in open(Path(sys.argv[1]) / "replay_navigation.jsonl")]
print(
    f"pull requests: {len(rows)}; truth files per PR median {statistics.median(len(r['truth']) for r in rows)}"
)
langs = {"python only": 0, "scripts only": 0, "both": 0}
for r in rows:
    py = any(p.endswith((".py", ".pyi")) for p in r["truth"])
    js = any(not p.endswith((".py", ".pyi")) for p in r["truth"])
    langs["both" if py and js else "python only" if py else "scripts only"] += 1
print("truth by language:", langs)


def metrics(order, truth):
    truth = set(truth)
    first = next((i for i, p in enumerate(order) if p in truth), None)
    return dict(
        hit1=bool(truth & set(order[:1])),
        hit3=bool(truth & set(order[:3])),
        hit10=bool(truth & set(order[:10])),
        rec10=len(truth & set(order[:10])) / len(truth),
        mrr=0 if first is None else 1 / (first + 1),
    )


def variant(r, alpha, gamma, top=5):
    base = dict(r["ranked"])
    scores = dict(base)
    heads = [p for p, _ in r["ranked"][:top]]
    for h in heads:
        s = base[h]
        for f in r["neighbours"].get(h, []):
            scores[f] = (
                max(scores.get(f, 0.0), base.get(f, 0.0) + alpha * s)
                if alpha
                else scores.get(f, 0.0)
            )
        for f, count in r["cochange"].get(h, []):
            if gamma:
                scores[f] = scores.get(f, 0.0) + gamma * s * min(1.0, count / 3)
    return [p for p, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])) if kv_ok(p)]


def kv_ok(path):
    return True


def fused(r, k=60):
    """Reciprocal-rank fusion of the product's order and BM25."""
    scores = {}
    for order in ([p for p, _ in r["ranked"]], [p for p, _ in r["bm"]]):
        for i, p in enumerate(order[:50]):
            scores[p] = scores.get(p, 0.0) + 1 / (k + i + 1)
    return [p for p, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]


def summarize(name, orders, subset):
    ms = [metrics(orders[i], rows[i]["truth"]) for i in subset]
    n = len(ms)
    return name, {k: sum(m[k] for m in ms) / n for k in ms[0]}, n


everything = list(range(len(rows)))
tune, judge = everything[0::2], everything[1::2]
systems = {
    "keyword search (BM25)": [[p for p, _ in r["bm"]] for r in rows],
    "product today": [[p for p, _ in r["ranked"]] for r in rows],
    "product + BM25 fused": [fused(r) for r in rows],
}
grid = [
    (a, g)
    for a, g in itertools.product([0, 0.15, 0.3, 0.5, 0.8], [0, 0.15, 0.3, 0.5, 0.8])
    if a or g
]
best = None
for a, g in grid:
    orders = [variant(r, a, g) for r in rows]
    _, m, _ = summarize("", orders, tune)
    key = (m["hit3"], m["rec10"], m["mrr"])
    if best is None or key > best[0]:
        best = (key, a, g, orders)
_, a, g, orders = best
systems[f"product + neighbours({a}) + co-change({g}) [chosen on first half]"] = orders
for name, a_, g_ in (
    ("product + import neighbours (0.3)", 0.3, 0),
    ("product + co-change (0.3)", 0, 0.3),
):
    systems[name] = [variant(r, a_, g_) for r in rows]

for label, subset in (("judged on the second half", judge), ("all pull requests", everything)):
    print(f"\n{label} (n={len(subset)}):")
    print(f"   {'system':70s} hit@1  hit@3  hit@10  recall@10  MRR")
    for name, orders in systems.items():
        _, m, n = summarize(name, orders, subset)
        print(
            f"   {name:70s} {m['hit1'] * n:4.0f}  {m['hit3'] * n:5.0f}  {m['hit10'] * n:6.0f}  {m['rec10']:9.1%}  {m['mrr']:.3f}"
        )


def paired(a, b, subset, key="hit3"):
    wins = sum(
        metrics(systems[a][i], rows[i]["truth"])[key]
        > metrics(systems[b][i], rows[i]["truth"])[key]
        for i in subset
    )
    losses = sum(
        metrics(systems[a][i], rows[i]["truth"])[key]
        < metrics(systems[b][i], rows[i]["truth"])[key]
        for i in subset
    )
    return wins, losses


names = list(systems)
print("\npaired on all pull requests (wins, losses) at hit@3 and recall@10:")
for name in names[1:]:
    print(
        f"   {name[:66]:66s} vs keyword search: hit@3 {paired(name, names[0], everything)}  recall@10 {paired(name, names[0], everything, 'rec10')}"
    )
print("rank time per brief, median seconds:", statistics.median(r["rank_s"] for r in rows))
