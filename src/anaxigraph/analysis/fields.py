"""Shared shape checks for model-facing review contracts."""

from __future__ import annotations

from typing import Any


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
