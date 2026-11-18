"""The reach report on a small repository whose suite really ran once."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reach_report  # noqa: E402


def test_reach_of_past_changes_and_what_would_lower_it(recorded, capsys):
    root, record = recorded
    options = ["--suite", "shop", "--record", str(record), "--history", "commits"]
    found = reach_report.main([str(root), *options, "--then", "local:shop/pkg/settings.py"])

    # Newest first: a test, two top-level changes that reach both users' tests, a function.
    today = [row["today"] for row in found["pull_requests"]]
    assert today == pytest.approx([1 / 3, 2 / 3, 2 / 3, 1 / 3], abs=0.001)
    assert found["number"] | {"mean": 0, "median": 0} == {
        "mean": 0,
        "median": 0,
        "nearly_all": 0,
        "under_fifth": 0,
        "whole_suite": 0,
        "left_out": 0,
    }
    assert found["number"]["mean"] == pytest.approx(1 / 2, abs=0.001)
    layers = found["layers"]
    assert layers["ran"] == pytest.approx(1 / 6, abs=0.001)
    assert layers["top_level"] == pytest.approx(1 / 3, abs=0.001)
    assert layers["careful"] == pytest.approx(0, abs=0.001) and layers["always"] == 0
    assert found["kinds"] == {
        "source, top level": 2,
        "source, inside functions": 1,
        "test or test helper": 1,
    }

    # The file that cost most is the one whose top level changed, and the part is its folder.
    assert [(row["path"], row["changes"]) for row in found["costly"]] == [
        ("shop/pkg/settings.py", 2),
        ("shop/pkg/labels.py", 1),
    ]
    assert [(row["part"], row["files"]) for row in found["parts"]] == [("shop/pkg/", 5)]

    # Keeping those changes local is the asked what-if and also the best one found.
    assert [row["mean"] for row in found["then"]] == [
        pytest.approx(1 / 2, abs=0.001),
        pytest.approx(1 / 6, abs=0.001),
    ]
    assert found["best"][-1]["mean"] == pytest.approx(1 / 6, abs=0.001)
    printed = capsys.readouterr().out
    assert "1. The number" in printed and "shop/pkg/settings.py" in printed


def test_patterns_and_parts():
    inside = reach_report.matcher("app/*.py,app/models/**")
    assert inside("app/config.py") and inside("app/models/a/b.py")
    assert not inside("app/services/x.py") and not inside("other/app/config.py")
    flat = [f"app/services/{head}_{n}.py" for head in ("pay", "mail") for n in range(20)]
    parts = reach_report.parts_of([*flat, "app/services/solo.py", "app/models/user.py"])
    assert parts["app/services/pay_3.py"] == "app/services/pay_*"
    assert parts["app/services/solo.py"] == "app/services/ (other files)"
    assert parts["app/models/user.py"] == "app/models/"
