"""Pure scoring and grep baselines for the historical-commit replay benchmark.

Nothing here imports AnaxiGraph, so the baselines stay independent of the system under test.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

STOP_WORDS = frozenset(
    "the and for with from into that this when only keep record verify add use support make "
    "after before then their its are not all".split()
)
_TEST_PART = re.compile(r"(^|/)(tests?|testing)(/|$)")


def is_test(path: str) -> bool:
    name = PurePosixPath(path).name
    return bool(_TEST_PART.search(path)) or name.startswith("test_") or name == "conftest.py"


def is_python(path: str) -> bool:
    return path.endswith(".py")


def task_words(message: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", message.lower()) if w not in STOP_WORDS}


def rank_of_first_hit(ranked: list[str], expected: set[str]) -> int | None:
    """1-based rank of the first expected path, or None when absent."""
    return next((index + 1 for index, path in enumerate(ranked) if path in expected), None)


def recall(found: set[str], expected: set[str]) -> float | None:
    return None if not expected else len(found & expected) / len(expected)


def precision(found: set[str], expected: set[str]) -> float | None:
    return None if not expected else len(found & expected) / len(found) if found else 0.0


def reciprocal_fusion(rankings: list[list[str]], limit: int) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, path in enumerate(dict.fromkeys(ranking), start=1):
            scores[path] = scores.get(path, 0) + 1 / (60 + rank)
    return sorted(scores, key=lambda path: (-scores[path], path))[:limit]


def grep_owners(files: dict[str, str], message: str, limit: int) -> list[str]:
    """Rank production files by distinct task words in their text and path."""
    words = task_words(message)
    scored = []
    for path, text in files.items():
        lowered, lowered_path = text.lower(), path.lower()
        score = sum(w in lowered for w in words) + sum(w in lowered_path for w in words)
        scored.append((-score, path))
    return [path for _, path in sorted(scored)[:limit]]


def module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(text: str, target: str) -> bool:
    dotted = module_name(target)
    if not dotted:
        return False
    package, _, stem = dotted.rpartition(".")
    patterns = [rf"^\s*(from|import)\s+{re.escape(dotted)}\b"]
    if package:
        patterns.append(rf"^\s*from\s+{re.escape(package)}\s+import\s+[^\n]*\b{re.escape(stem)}\b")
    patterns += [
        rf"^\s*from\s+\.{re.escape(stem)}\s+import\b",
        rf"^\s*from\s+\.\s+import\s+[^\n]*\b{re.escape(stem)}\b",
    ]
    return any(re.search(p, text, re.MULTILINE) for p in patterns)


def grep_importers(files: dict[str, str], targets: set[str], depth: int = 2) -> set[str]:
    """Files that import a target by name, followed transitively like AnaxiGraph's two levels."""
    found, frontier = set(targets), set(targets)
    for _ in range(depth):
        frontier = {
            path
            for path, text in files.items()
            if path not in found and any(_imports(text, target) for target in frontier)
        }
        found |= frontier
    return found


def grep_tests(files: dict[str, str], targets: set[str]) -> set[str]:
    """Test files mentioning a target module's stem or importing it."""
    stems = {PurePosixPath(t).stem for t in targets} - {"__init__"}
    return {
        path
        for path, text in files.items()
        if is_test(path)
        and (
            any(re.search(rf"\b{re.escape(s)}\b", text) for s in stems)
            or any(_imports(text, t) for t in targets)
        )
    }


def ranked_cochanged(history: list[set[str]], target: str, limit: int) -> list[str]:
    """Files most often changed in the same earlier commits as target (logical coupling)."""
    counts: dict[str, int] = {}
    for changed in history:
        if target in changed and len(changed) <= 30:
            for path in changed - {target}:
                counts[path] = counts.get(path, 0) + 1
    ranked = sorted(counts, key=lambda path: (-counts[path], path))
    return ranked[:limit]


def cochanged(history: list[set[str]], target: str, limit: int) -> set[str]:
    return set(ranked_cochanged(history, target, limit))


def read_python(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8", errors="ignore")
        for path in sorted(root.rglob("*.py"))
        if not any(part.startswith(".") for part in path.relative_to(root).parts)
    }


def summarize(rows: list[dict]) -> dict:
    """Aggregate per-commit rows; None metrics are excluded from their denominators."""

    def hits(key: str, k: int) -> list[int]:
        return [int(r[key] is not None and r[key] <= k) for r in rows if key in r]

    def mean(key: str) -> dict | None:
        values = [r[key] for r in rows if r.get(key) is not None]
        return (
            None if not values else {"mean": round(sum(values) / len(values), 3), "n": len(values)}
        )

    owners = {}
    for method in (
        "mapped",
        "mapped_uncapped",
        "structural",
        "structural_uncapped",
        "grep",
        "symbols",
        "fused",
    ):
        key = f"{method}_owner_rank"
        if any(key in r for r in rows):
            owners[method] = {
                f"hit@{k}": f"{sum(hits(key, k))}/{len(hits(key, k))}" for k in (1, 3)
            }
    metrics = {}
    for key in sorted({k for r in rows for k in r if k.startswith(("oracle_", "task_"))}):
        metrics[key] = mean(key)
    eligible_sizes = {}
    for group in ("consumer", "test"):
        eligible = [r for r in rows if r.get(f"oracle_{group}_recall_anaxigraph") is not None]
        for method in ("anaxigraph", "grep", "history", "combined"):
            values = [
                r[f"oracle_{group}_size_{method}"]
                for r in eligible
                if f"oracle_{group}_size_{method}" in r
            ]
            if values:
                eligible_sizes[f"{group}_{method}"] = {
                    "mean": round(sum(values) / len(values), 3),
                    "n": len(values),
                }
    return {
        "commits": len(rows),
        "owners": owners,
        "metrics": metrics,
        "eligible_list_sizes": eligible_sizes,
    }
