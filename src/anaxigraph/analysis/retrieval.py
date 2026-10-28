"""Explainable module retrieval from captured facts and current semantic annotations."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

from anaxigraph.contracts import Graph, Node

_STOP = set(
    "the and for with from into that this when only keep verify add use support make "
    "after before then their its are not all change improve implement should existing".split()
)


def _terms(text: str) -> set[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return {word for word in re.findall(r"[a-z][a-z0-9]{2,}", text.lower()) if word not in _STOP}


def _mentioned(task: str, identity: str) -> bool:
    return bool(re.search(r"(?<![\w/])" + re.escape(identity) + r"(?![\w/])", task, re.I))


def _semantic(module: Node) -> str:
    if module.data.get("annotation_status") != "current":
        return ""
    annotation = module.data.get("annotation", {})
    return " ".join(
        [annotation.get("overview", ""), annotation.get("detail", "")]
        + annotation.get("capabilities", [])
        + [item.get("overview", "") for item in annotation.get("functions", [])]
        + [item.get("statement", "") for item in annotation.get("contracts", [])]
    )


def _match(module, symbols, source_terms, task, weights) -> dict[str, Any]:
    words = weights.keys()
    exact = next((node for node in symbols if _mentioned(task, node.name)), module)
    path_match = _mentioned(task, module.path) or _mentioned(task, module.path.rsplit("/", 1)[-1])
    fields = {
        "path": (words & _terms(module.path), 2),
        "symbols": (words & _terms(" ".join(node.name for node in symbols)), 2),
        "source": (words & source_terms, 1),
        "current_annotation": (words & _terms(_semantic(module)), 1),
    }
    scores = {
        name: round(sum(weights[word] for word in terms) * weight, 3)
        for name, (terms, weight) in fields.items()
        if terms
    }
    reasons = [
        f"{name}: {', '.join(sorted(terms)[:4])}" for name, (terms, _) in fields.items() if terms
    ]
    if path_match or exact is not module:
        kind = "exact_path" if path_match else "exact_symbol"
        scores[kind] = 20.0
        reasons.insert(0, f"{kind}: {module.path if path_match else exact.name}")
    return {
        "id": module.id,
        "name": module.name,
        "path": module.path,
        "score": round(sum(scores.values()), 3),
        "match_scores": scores,
        "match_reasons": reasons,
        "evidence": [{"path": module.path, "line": exact.line}],
        "matched_symbol": exact.name,
        "annotation_status": module.data.get("annotation_status", "missing"),
    }


def rank_owners(
    graph: Graph, sources: dict[str, str], task: str, limit: int = 12
) -> list[dict[str, Any]]:
    """Aggregate before limiting; repeated symbols cannot crowd out other modules.

    Scores are retrieval hints, not confidence or proof of ownership. Stale annotations
    contribute no terms. Nothing reads or executes project code here.
    """
    modules = {
        node.path: node
        for node in graph.nodes
        if node.kind == "module"
        and node.path.endswith((".py", ".pyi"))
        and node.data.get("parse_status", "parsed") == "parsed"
    }
    symbols: dict[str, list[Node]] = defaultdict(list)
    for node in graph.nodes:
        if node.kind in {"function", "class", "interface"}:
            symbols[node.path].append(node)
    words = _terms(task)
    documents = {path: _terms(text) for path, text in sources.items()}
    frequency = Counter(word for terms in documents.values() for word in words & terms)
    weights = {word: 1 + math.log((1 + len(documents)) / (1 + frequency[word])) for word in words}
    ranked = [
        _match(node, symbols[path], documents.get(path, set()), task, weights)
        for path, node in modules.items()
    ]
    ranked = [row for row in ranked if row["score"]]
    return sorted(
        ranked,
        key=lambda row: (
            bool(modules[row["path"]].data.get("is_test")),
            -int("exact_path" in row["match_scores"]),
            -int("exact_symbol" in row["match_scores"]),
            -row["score"],
            row["path"],
        ),
    )[:limit]
