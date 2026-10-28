"""Historical associations suggest investigation; they never establish code dependencies."""

from __future__ import annotations

from collections import Counter


def cochange_advice(history: dict, targets: set[str], limit: int = 6) -> dict:
    counts: Counter[str] = Counter()
    witnesses: dict[str, set[str]] = {}
    support = 0
    for change in history.get("changes", []):
        paths = set(change["paths"])
        sources = paths & targets
        if not sources or change["size"] > 30:
            continue
        support += 1
        for path in paths - targets:
            counts[path] += 1
            witnesses.setdefault(path, set()).update(sources)
    return {
        **{key: value for key, value in history.items() if key != "changes"},
        "basis": "historical-co-change",
        "supporting_commits": support,
        "suggestions": [
            {
                "path": path,
                "cochange_commits": counts[path],
                "with_paths": sorted(witnesses[path])[:3],
                "support_fraction": round(counts[path] / support, 3),
            }
            for path in sorted(counts, key=lambda path: (-counts[path], path))[:limit]
        ],
        "suggestions_total": len(counts),
        "interpretation": "Inspect these associations; they do not require edits or tests, prove dependencies, or authorize skipping checks.",
    }
