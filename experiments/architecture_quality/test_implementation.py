"""The implementation probe's oracle and briefs are checked without model calls."""

import asyncio

from .implementation import SOURCES, fixture, peak_concurrency


def test_fixture_oracle_exposes_real_concurrency_difference():
    assert asyncio.run(peak_concurrency(SOURCES["unbounded"])) == 20
    assert asyncio.run(peak_concurrency(SOURCES["bounded"])) == 4


def test_probe_holds_semantic_briefs_and_public_signatures_constant(tmp_path):
    modules = []
    for name, source in SOURCES.items():
        app = fixture(tmp_path / name, source)
        request = app.analysis_request(
            "patterns", ["delivery.py"], pattern_ids=["unbounded-concurrency"]
        )
        modules.append(request["packet"]["modules"][0])
        assert request["implementation_evidence"] == {"delivery.py": source}
    assert modules[0]["brief"] == modules[1]["brief"]
    assert modules[0]["facts"]["public_surface"] == modules[1]["facts"]["public_surface"]
