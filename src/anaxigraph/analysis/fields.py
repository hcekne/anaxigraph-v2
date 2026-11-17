"""Shared shape checks and bounded views for model-facing review contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from anaxigraph.contracts import Graph


def fingerprint(value: Any) -> str:
    body = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(body.encode()).hexdigest()


def proposal_id(row: dict, finding: dict) -> str:
    """Keep reuse identities stable, including the historical Pattern identity format."""
    origin = (row["result"].get("provenance") or {}).get("original_id") or row["id"]
    if row["kind"] == "patterns":
        finding = finding | {"review_id": origin}
    return f"{origin}:{fingerprint(finding)}"


def short(value: Any, maximum: int = 240) -> str:
    text = value if isinstance(value, str) else ""
    raw = text.encode("utf-8")
    return text if len(raw) <= maximum else raw[: maximum - 3].decode("utf-8", "ignore") + "..."


def references(value: Any, graph: Graph) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {"path": item["path"], "line": item["line"]}
        for item in value
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and item.get("path") in graph.snapshot.files
        and type(item.get("line")) is int
        and item["line"] > 0
    ][:2]


def project(value: Any, contract: Any) -> Any:
    """Keep delivered response fields without inventing or repairing required values."""
    if isinstance(value, dict) and isinstance(contract, dict):
        return {key: project(item, contract[key]) for key, item in value.items() if key in contract}
    if isinstance(value, list) and isinstance(contract, list):
        item_contract = contract[0] if contract else {}
        return [project(item, item_contract) for item in value]
    return value


def text(value: Any, field: str, limit: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be nonempty text of at most {limit} characters")
    return value.strip()


def texts(value: Any, field: str, *, required=False, count=12, limit=300) -> list[str]:
    if not isinstance(value, list) or len(value) > count or (required and not value):
        raise ValueError(f"{field} must be a bounded list of text")
    return [text(item, field, limit) for item in value]


def assessments(value: Any, expected: set[str], key: str) -> dict[str, str]:
    if not isinstance(value, list) or len(value) != len(expected):
        subject = "scoped module" if key == "module_id" else "requested pipeline"
        raise ValueError(f"Review assessments must cover every {subject} exactly once")
    outcomes = {}
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Review assessment must be an object")
        identity, outcome = item.get(key), item.get("outcome")
        if not isinstance(identity, str) or identity not in expected or identity in outcomes:
            raise ValueError("Review assessments contain duplicate or unexpected identities")
        if outcome not in ("finding", "keep", "unknown"):
            raise ValueError("Review outcome must be finding, keep or unknown")
        outcomes[identity] = outcome
    return outcomes


def members(value: Any, expected: set[str], label: str = "scoped module") -> set[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise ValueError(f"Review members must be distinct {label} IDs")
    if len(set(value)) != len(value) or not set(value) <= expected:
        raise ValueError(f"Review members must be distinct {label} IDs")
    return set(value)
