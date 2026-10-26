"""Versioned contract adjudication; the original as-run checker remains unchanged."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import acceptance


def check_trip(item: dict[str, Any], *, days: int = 5, start: str = acceptance.START) -> None:
    """Check the published shape without inventing integer/score/unknown semantics."""
    assert item["resort"]["id"], "resort.id is required"
    forecast = item["forecast"]
    assert isinstance(forecast, list) and len(forecast) == 7, "forecast needs seven daily rows"
    expected = [(date.fromisoformat(start) + timedelta(days=i)).isoformat() for i in range(7)]
    assert [day["date"] for day in forecast] == expected, (
        "forecast dates must match requested start"
    )
    for day in forecast:
        assert day["snowfall_cm"] is None or acceptance.finite(day["snowfall_cm"]), (
            "daily snowfall must be nonnegative or unknown"
        )
        assert day["snow_depth_cm"] is None or acceptance.finite(day["snow_depth_cm"]), (
            "snow depth must be nonnegative or unknown"
        )
        probability = day["powder_probability"]
        assert probability is None or acceptance.finite(probability) and probability <= 1, (
            "powder probability must be0..1 or unknown"
        )
        assert isinstance(day["probability_basis"], str) and day["probability_basis"], (
            "powder probability needs its basis"
        )
    cost = item["cost"]
    assert cost["currency"] == "GBP", "requested currency must be preserved"
    assert all(
        acceptance.finite(cost[key]) for key in ("transport", "accommodation", "lifts", "total")
    ), "cost components must be finite and nonnegative"
    assert (
        abs(cost["total"] - sum(cost[key] for key in ("transport", "accommodation", "lifts")))
        <= 0.011
    ), "cost total must match its components"
    assert cost["basis"] in {"estimate", "live_quote"} and cost["updated_at"], (
        "cost needs its basis/update time"
    )
    assert acceptance.finite(item["travel"]["hours"]) and item["travel"]["basis"], (
        "travel needs nonnegative hours and its basis"
    )
    powder_days = item["powder_days"]
    assert powder_days is None or acceptance.finite(powder_days) and powder_days <= days, (
        "expected powder_days must be finite0..requested days or unknown"
    )
    assert "score" in item, "score is required; its JSON type was not specified"
    assert isinstance(item["warnings"], list), "warnings must be a list"
    amounts = [day["snowfall_cm"] for day in forecast[:days]]
    total = item["snowfall_total_cm"]
    if all(value is not None for value in amounts):
        assert acceptance.finite(total) and abs(total - sum(amounts)) <= 0.01, (
            "fully known snowfall total must match requested days"
        )
    elif total is not None:
        assert acceptance.finite(total), "a partial snowfall total must be finite and nonnegative"
        warning = " ".join(str(text) for text in item["warnings"]).casefold()
        partial = re.search(
            r"\b(partial|incomplete)\b|\bknown\b.{0,60}\bonly\b|\btotal\b.{0,60}\bexcludes?\b.{0,60}\b(missing|unknown)\b",
            warning,
        )
        assert partial, (
            "a numeric total with missing daily data needs an explicit partial-total warning"
        )
    assert item["sources"], "sources are required"
    for source in item["sources"]:
        assert all(source.get(key) for key in ("name", "url", "updated_at", "kind")), (
            "each source needs name/url/update time/kind"
        )


class ChecksV2(acceptance.Checks):
    def __init__(self, base_url: str, timeout: float = 10) -> None:
        super().__init__(base_url, timeout)
        self.unmeasured: list[dict[str, str]] = []

    def trip_contract(self) -> None:
        items = self.search()
        assert len(items) >= 6, "Default deterministic catalogue is incomplete"
        for item in items:
            try:
                check_trip(item)
            except (AssertionError, KeyError, TypeError) as error:
                identity = item.get("resort", {}).get("id", "unknown resort")
                raise AssertionError(f"{identity}: {error}") from error

    def sorting(self) -> None:
        for name, extract, reverse in (
            ("cost", lambda item: item["cost"]["total"], False),
            ("travel", lambda item: item["travel"]["hours"], False),
            ("powder", lambda item: item["snowfall_total_cm"], True),
        ):
            values = [extract(item) for item in self.search(sort=name)]
            expected = sorted(
                values,
                key=lambda value: (
                    value is None,
                    -value if reverse and value is not None else value or 0,
                ),
            )
            assert values == expected, f"Incorrect {name} order (unknown values belong last)"
        items = self.search(sort="confidence")
        if (
            not items
            or any("confidence" not in item for item in items)
            or any(
                item["confidence"] is not None
                and not acceptance.finite(item["confidence"], minimum=-float("inf"))
                for item in items
            )
            or not any(item["confidence"] is not None for item in items)
        ):
            self.unmeasured.append(
                {
                    "check": "confidence_order",
                    "reason": "No explicit comparable numeric confidence field across results; no inference from powder heuristics.",
                }
            )
            return
        values = [item["confidence"] for item in items]
        expected = sorted(
            values, key=lambda value: (value is None, -value if value is not None else 0)
        )
        assert values == expected, (
            "Explicit comparable confidence must be descending with unknowns last"
        )


def evaluate(base_url: str, milestone: int = 4) -> dict[str, Any]:
    checks = ChecksV2(base_url)
    cases = [
        ("branded_frontend", checks.home),
        ("service_status", checks.status),
        ("catalogue_pagination_detail", checks.catalogue),
        ("trip_contract", checks.trip_contract),
    ]
    if milestone >= 2:
        cases += [
            ("ranking_order", checks.sorting),
            ("budget_and_snow_filters", checks.filters),
            ("currencies_and_trip_duration", checks.currencies_and_duration),
            ("invalid_input", checks.malformed),
        ]
    if milestone >= 4:
        cases += [("subsequent_change_nights_and_travel", checks.maintenance)]
    for name, function in cases:
        checks.run(name, function)
    passed = sum(item["status"] == "passed" for item in checks.results)
    return {
        "schema_version": 1,
        "assessment_version": 2,
        "kind": "http_acceptance_adjudicated",
        "base_url": base_url,
        "milestone": milestone,
        "passed": passed,
        "total": len(checks.results),
        "status": "passed" if passed == len(checks.results) else "failed",
        "checks": checks.results,
        "unmeasured_details": checks.unmeasured,
        "not_measured": [
            "visual_usability",
            "architectural_quality",
            "live_provider_correctness",
            "forecast_calibration",
            "production_scalability",
        ]
        + [x["check"] for x in checks.unmeasured],
        "amendment": "amendment-v2.json",
        "original_as_run_replaced": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--milestone", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(args.base_url, args.milestone)
    raw = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(raw)
    print(raw, end="")
    return int(result["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
