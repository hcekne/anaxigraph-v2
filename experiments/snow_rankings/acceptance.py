"""Shared black-box checks for both Snow Rankings implementations (stdlib only).

Run this from the evaluation checkout, not as implementation-specific unit tests.
Passing these checks establishes the declared HTTP behavior, not architectural quality.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

START = "2026-01-15"


def finite(value: Any, *, minimum: float = 0) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= minimum


class Checks:
    def __init__(self, base_url: str, timeout: float = 10) -> None:
        self.base_url, self.timeout = base_url.rstrip("/"), timeout
        self.results: list[dict[str, Any]] = []

    def request(self, path: str, query: dict | None = None) -> tuple[int, Any]:
        url = self.base_url + path + ("?" + urlencode(query) if query else "")
        request = Request(url, headers={"Accept": "application/json"})
        try:
            response = urlopen(request, timeout=self.timeout)
        except HTTPError as error:
            return error.code, None
        with response:
            raw = response.read(2_000_001)
            assert len(raw) <= 2_000_000, "Response exceeds the acceptance response budget"
            if "json" in response.headers.get("Content-Type", ""):
                return response.status, json.loads(raw)
            return response.status, raw.decode("utf-8")

    def run(self, name, function) -> None:
        started = time.monotonic()
        try:
            function()
        except Exception as error:
            self.results.append(
                {
                    "name": name,
                    "status": "failed",
                    "detail": str(error)[:500],
                    "elapsed_seconds": round(time.monotonic() - started, 4),
                }
            )
        else:
            self.results.append(
                {
                    "name": name,
                    "status": "passed",
                    "elapsed_seconds": round(time.monotonic() - started, 4),
                }
            )

    def search(self, **options) -> list[dict]:
        query = {
            "origin": "London",
            "start_date": START,
            "days": 5,
            "currency": "GBP",
            "sort": "best",
            "limit": 100,
        } | options
        status, result = self.request("/api/search", query)
        assert status == 200, f"Search returned HTTP {status}"
        assert isinstance(result, dict) and isinstance(result.get("items"), list)
        assert type(result.get("total")) is int and result["total"] >= len(result["items"])
        return result["items"]

    def home(self) -> None:
        status, html = self.request("/")
        assert status == 200 and "Snow Rankings" in html, "Missing usable branded HTML page"

    def status(self) -> None:
        code, status = self.request("/api/status")
        assert code == 200 and status.get("status") == "ok"
        assert status.get("data_mode") in {"demo", "mixed", "live"}
        assert type(status.get("resorts")) is int and status["resorts"] >= 6

    def catalogue(self) -> None:
        code, catalogue = self.request("/api/resorts", {"limit": 100, "offset": 0})
        assert code == 200 and catalogue["total"] >= 6
        assert len({item["country"] for item in catalogue["items"]}) >= 4
        for item in catalogue["items"]:
            assert isinstance(item["id"], str) and item["id"]
            assert isinstance(item["name"], str) and item["name"]
            assert finite(item["latitude"], minimum=-90) and item["latitude"] <= 90
            assert finite(item["longitude"], minimum=-180) and item["longitude"] <= 180
        code, page = self.request("/api/resorts", {"limit": 2, "offset": 0})
        assert code == 200 and len(page["items"]) == 2 and page["total"] == catalogue["total"]
        identity = catalogue["items"][0]["id"]
        code, detail = self.request("/api/resorts/" + identity)
        assert code == 200 and isinstance(detail.get("forecast"), list)
        assert detail.get("sources"), "Missing detail provenance"
        assert self.request("/api/resorts/does-not-exist")[0] == 404

    def trip_contract(self) -> None:
        items = self.search()
        assert len(items) >= 6, "Default deterministic catalogue is incomplete"
        for item in items:
            assert item["resort"]["id"]
            forecast = item["forecast"]
            assert len(forecast) == 7
            expected = [
                (date.fromisoformat(START) + timedelta(days=i)).isoformat() for i in range(7)
            ]
            assert [day["date"] for day in forecast] == expected
            for day in forecast:
                assert day["snowfall_cm"] is None or finite(day["snowfall_cm"])
                assert day["snow_depth_cm"] is None or finite(day["snow_depth_cm"])
                probability = day["powder_probability"]
                assert probability is None or finite(probability) and probability <= 1
                assert isinstance(day["probability_basis"], str) and day["probability_basis"]
            cost = item["cost"]
            assert cost["currency"] == "GBP"
            assert all(
                finite(cost[key]) for key in ("transport", "accommodation", "lifts", "total")
            )
            assert (
                abs(
                    cost["total"]
                    - sum(cost[key] for key in ("transport", "accommodation", "lifts"))
                )
                <= 0.011
            )
            assert cost["basis"] in {"estimate", "live_quote"} and cost["updated_at"]
            assert finite(item["travel"]["hours"]) and item["travel"]["basis"]
            assert type(item["powder_days"]) is int and 0 <= item["powder_days"] <= 5
            assert finite(item["score"], minimum=-float("inf"))
            expected_total = sum(day["snowfall_cm"] or 0 for day in forecast[:5])
            assert abs(item["snowfall_total_cm"] - expected_total) <= 0.01
            assert isinstance(item["warnings"], list) and item["sources"]
            for source in item["sources"]:
                assert all(source.get(key) for key in ("name", "url", "updated_at", "kind"))

    def sorting(self) -> None:
        for name, extract, reverse in (
            ("cost", lambda item: item["cost"]["total"], False),
            ("travel", lambda item: item["travel"]["hours"], False),
            ("powder", lambda item: item["snowfall_total_cm"], True),
        ):
            values = [extract(item) for item in self.search(sort=name)]
            assert values == sorted(values, reverse=reverse), f"Incorrect {name} order"
        unknown_seen = False
        for item in self.search(sort="confidence"):
            known = any(day["powder_probability"] is not None for day in item["forecast"][:5])
            assert not (known and unknown_seen), (
                "Unknown confidence sorts ahead of available evidence"
            )
            unknown_seen = unknown_seen or not known

    def filters(self) -> None:
        items = self.search()
        assert self.search(budget=0) == [], "Budget is not applied to displayed totals"
        assert self.search(min_snowfall_cm=max(i["snowfall_total_cm"] for i in items) + 1) == []
        budget = sorted(i["cost"]["total"] for i in items)[len(items) // 2]
        assert all(item["cost"]["total"] <= budget for item in self.search(budget=budget))

    def currencies_and_duration(self) -> None:
        base = {item["resort"]["id"]: item for item in self.search()}
        for currency, rate in (("EUR", 1.17), ("USD", 1.28)):
            for item in self.search(currency=currency):
                assert item["cost"]["currency"] == currency
                assert (
                    abs(item["cost"]["total"] - base[item["resort"]["id"]]["cost"]["total"] * rate)
                    <= 0.05
                )
        for item in self.search(days=1):
            previous = base[item["resort"]["id"]]
            assert item["cost"]["transport"] == previous["cost"]["transport"]
            assert item["cost"]["accommodation"] < previous["cost"]["accommodation"]
            assert item["cost"]["lifts"] < previous["cost"]["lifts"]

    def malformed(self) -> None:
        for options in (
            {"days": 0},
            {"days": 8},
            {"currency": "ZZZ"},
            {"start_date": "not-a-date"},
            {"origin": "91,181"},
        ):
            code, _ = self.request(
                "/api/search", {"origin": "London", "start_date": START} | options
            )
            assert 400 <= code < 500, f"Invalid input accepted: {options}"
        assert self.search(origin="51.5074,-0.1278"), "Coordinate origin is not supported"

    def maintenance(self) -> None:
        default = {i["resort"]["id"]: i for i in self.search(days=5)}
        shorter = self.search(days=5, nights=3)
        assert shorter
        for item in shorter:
            before = default[item["resort"]["id"]]
            assert item["cost"]["accommodation"] < before["cost"]["accommodation"]
            assert item["cost"]["lifts"] == before["cost"]["lifts"]
            assert item["cost"]["transport"] == before["cost"]["transport"]
        filtered = self.search(max_travel_hours=8)
        assert filtered and all(item["travel"]["hours"] <= 8 for item in filtered)
        code, _ = self.request(
            "/api/search", {"origin": "London", "start_date": START, "nights": -1}
        )
        assert 400 <= code < 500


def evaluate(base_url: str, milestone: int = 4) -> dict[str, Any]:
    checks = Checks(base_url)
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
        "kind": "http_acceptance",
        "base_url": base_url,
        "milestone": milestone,
        "passed": passed,
        "total": len(checks.results),
        "status": "passed" if passed == len(checks.results) else "failed",
        "checks": checks.results,
        "not_measured": [
            "visual_usability",
            "architectural_quality",
            "live_provider_correctness",
            "forecast_calibration",
            "production_scalability",
        ],
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
