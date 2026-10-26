"""Normalize provider-reported usage without guessing missing tokens or prices.

Codex: https://learn.chatgpt.com/docs/non-interactive-mode
Claude: https://code.claude.com/docs/en/headless
Claude cache tokens are additional to its input_tokens; Codex's are a subset.
"""

from __future__ import annotations

import json
import math
from typing import Any

FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "reasoning_output_tokens",
    "total_tokens",
    "cost_usd",
)


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)):
        return None
    return value if value >= 0 and math.isfinite(value) else None


def _objects(value: str | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, str) or len(value.encode()) > 8_000_000:
        return []
    objects = []
    for line in value.splitlines():
        try:
            item = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(item, dict):
            objects.append(item)
    if not objects:
        try:
            item = json.loads(value)
            if isinstance(item, dict):
                return [item]
        except (ValueError, TypeError):
            pass
    return objects


def _codex(events: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [
        event["usage"]
        for event in events
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict)
    ]
    if not completed:
        return {}
    result: dict[str, Any] = {"usage_source": "codex-turn.completed"}
    for key in FIELDS[:-1]:
        values = [_number(item.get(key), integer=True) for item in completed]
        if all(value is not None for value in values):
            result[key] = sum(value for value in values if value is not None)
    if result.get("total_tokens") is None and all(
        key in result for key in ("input_tokens", "output_tokens")
    ):
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def _claude(events: list[dict[str, Any]]) -> dict[str, Any]:
    result = next((item for item in reversed(events) if item.get("type") == "result"), None)
    if result is None:
        return {}
    usage = result.get("usage")
    normalized: dict[str, Any] = {}
    if isinstance(usage, dict):
        mapping = {
            "input_tokens": "uncached_input_tokens",
            "output_tokens": "output_tokens",
            "cache_read_input_tokens": "cached_input_tokens",
            "cache_creation_input_tokens": "cache_write_input_tokens",
        }
        for original, target in mapping.items():
            value = _number(usage.get(original), integer=True)
            if value is not None:
                normalized[target] = value
        keys = ("uncached_input_tokens", "cached_input_tokens", "cache_write_input_tokens")
        if all(key in normalized for key in keys):
            normalized["input_tokens"] = sum(normalized[key] for key in keys)
        if all(key in normalized for key in ("input_tokens", "output_tokens")):
            normalized["total_tokens"] = normalized["input_tokens"] + normalized["output_tokens"]
        normalized["usage_source"] = "claude-result"
    cost = _number(result.get("total_cost_usd"))
    if cost is not None:
        normalized.update(cost_usd=cost, cost_source="provider-reported-estimate")
    return normalized


def provider_usage(provider: str, value: str | dict[str, Any]) -> dict[str, Any]:
    """Read only usage metadata; model text is never returned or interpreted as usage."""
    if provider not in {"codex", "claude"}:
        raise ValueError("Usage provider must be codex or claude")
    events = _objects(value)
    found = _codex(events) if provider == "codex" else _claude(events)
    return (
        {key: None for key in FIELDS}
        | {"usage_source": "unknown", "cost_source": "unknown"}
        | found
    )
