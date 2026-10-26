"""V2 corrects contract mismatches without weakening actual numeric obligations."""

from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import acceptance_v2
import adjudicate


def trip():
    return {
        "resort": {"id": "fixture"},
        "forecast": [
            {
                "date": (date(2026, 1, 15) + timedelta(days=i)).isoformat(),
                "snowfall_cm": i + 1,
                "snow_depth_cm": None,
                "powder_probability": 0.5,
                "probability_basis": "Synthetic heuristic",
            }
            for i in range(7)
        ],
        "cost": {
            "currency": "GBP",
            "transport": 100,
            "accommodation": 150,
            "lifts": 50,
            "total": 300,
            "basis": "estimate",
            "updated_at": "2026-01-15T00:00:00Z",
        },
        "travel": {"hours": 5, "basis": "estimate"},
        "powder_days": 2.5,
        "score": {"value": 0.8, "explanation": "Structured score is not forbidden by the contract"},
        "snowfall_total_cm": 15,
        "warnings": [],
        "sources": [
            {"name": "fixture", "url": "/fixture", "updated_at": "2026-01-15", "kind": "synthetic"}
        ],
    }


class ContractTests(unittest.TestCase):
    def test_fractional_unknown_and_integer_days_with_structured_score_are_valid(self):
        for value in (0, 2.75, 5, None):
            item = trip()
            item["powder_days"] = value
            acceptance_v2.check_trip(item)
        item = trip()
        del item["score"]
        with self.assertRaisesRegex(AssertionError, "score is required"):
            acceptance_v2.check_trip(item)

    def test_invalid_expected_days_remain_invalid(self):
        for value in (-0.01, 5.01, True, float("nan")):
            item = trip()
            item["powder_days"] = value
            with self.assertRaisesRegex(AssertionError, "powder_days"):
                acceptance_v2.check_trip(item)

    def test_known_aggregate_must_match_but_unknowns_are_not_forced_to_zero(self):
        item = trip()
        item["snowfall_total_cm"] = 16
        with self.assertRaisesRegex(AssertionError, "fully known snowfall total"):
            acceptance_v2.check_trip(item)
        item = trip()
        item["forecast"][0]["snowfall_cm"] = None
        item["snowfall_total_cm"] = None
        acceptance_v2.check_trip(item)
        item["snowfall_total_cm"] = 14
        item["warnings"] = ["Partial snowfall total covers known days only."]
        acceptance_v2.check_trip(item)
        item["warnings"] = []
        with self.assertRaisesRegex(AssertionError, "partial-total warning"):
            acceptance_v2.check_trip(item)

    def test_confidence_is_not_inferred_from_powder_probability(self):
        class Fixture(acceptance_v2.ChecksV2):
            def __init__(self, confidence):
                super().__init__("http://unused.invalid")
                self.confidence = confidence

            def search(self, **options):
                if options.get("sort") != "confidence":
                    return [trip()]
                items = [copy.deepcopy(trip()) for _ in self.confidence]
                for item, value in zip(items, self.confidence):
                    item["confidence"] = value
                return items

        checks = Fixture([None, None])
        checks.sorting()
        self.assertEqual(checks.unmeasured[0]["check"], "confidence_order")
        Fixture([0.8, 0.4, None]).sorting()
        with self.assertRaisesRegex(AssertionError, "Explicit comparable confidence"):
            Fixture([0.8, None, 0.4]).sorting()

    def test_frozen_startup_uses_existing_python_but_snapshot_source(self):
        parent = Path.home() / ".local/state/anaxigraph/experiments"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            root = Path(temporary)
            repository, snapshot = root / "repo", root / "snapshot"
            python = repository / ".venv/bin/python"
            python.parent.mkdir(parents=True)
            python.write_text("fixture")
            command = adjudicate.launch_args(
                [
                    "uv",
                    "run",
                    "--cache-dir",
                    str(root / "cache"),
                    "python",
                    "-m",
                    "snow_rankings",
                    "serve",
                    "--port",
                    "{port}",
                    "--data",
                    str(repository / "data/seed.json"),
                ],
                repository,
                snapshot,
                18790,
            )
            self.assertEqual(command[0], str(python))
            self.assertIn(str(snapshot / "data/seed.json"), command)
            self.assertIn("18790", command)
            self.assertNotIn("uv", command)


if __name__ == "__main__":
    unittest.main()
