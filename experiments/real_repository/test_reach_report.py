"""The reach report on a small repository whose suite really ran once."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from anaxigraph import recorder
from anaxigraph.application import Application

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reach_report  # noqa: E402

FILES = {
    ".gitignore": "__pycache__/\n.anaxigraph-v2/\n",
    "shop/pkg/__init__.py": "",
    "shop/pkg/settings.py": "LIMIT = 2\n",
    "shop/pkg/prices.py": (
        "from pkg.settings import LIMIT\n\n\ndef total(items):\n    return sum(items[:LIMIT])\n"
    ),
    "shop/pkg/orders.py": (
        "from pkg.prices import total\n\n\ndef place(items):\n    return {'total': total(items)}\n"
    ),
    "shop/pkg/labels.py": "def label(order):\n    return str(order['total'])\n",
    "shop/tests/test_prices.py": (
        "from pkg.prices import total\n\n\ndef test_total():\n    assert total([1, 2, 3]) == 6\n"
    ),
    "shop/tests/test_orders.py": (
        "from pkg.orders import place\n\n\ndef test_place():\n"
        "    assert place([1, 2, 3])['total'] == 6\n"
    ),
    "shop/tests/test_labels.py": (
        "from pkg.labels import label\n\n\ndef test_label():\n"
        "    assert label({'total': 5}).endswith('5')\n"
    ),
}
# Three changes after the first commit: inside a function, at the top level, and to a test.
LATER = [
    ("shop/pkg/labels.py", "def label(order):\n    return f\"Order {order['total']}\"\n"),
    ("shop/pkg/settings.py", "LIMIT = 3\n"),
    (
        "shop/tests/test_labels.py",
        "from pkg.labels import label\n\n\ndef test_label():\n"
        "    assert label({'total': 5}) == 'Order 5'\n",
    ),
]


def git(root: Path, *args: str) -> None:
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    subprocess.run(["git", "-C", str(root), *identity, *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def recorded(tmp_path_factory) -> tuple[Path, Path]:
    """A checkout with four commits, and the record of its suite at the last one with every
    test file given the same duration."""
    reports = tmp_path_factory.mktemp("reach")
    root = reports / "checkout"
    for relative, content in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Start")
    for relative, content in LATER:
        (root / relative).write_text(content)
        git(root, "commit", "-q", "-am", f"Change {relative}")
    app = Application(root)
    environment = os.environ | {
        "PYTHONPATH": str(Path(recorder.__file__).parent),
        "ANAXIGRAPH_READS": str(reports / "reads.json"),
        "ANAXIGRAPH_RECORDING": app.start_recording("shop")["manifest"],
        "COVERAGE_FILE": str(reports / "shop.coverage"),
    }
    command = [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]
    command += ["-p", "anaxigraph_reads", "--cov=pkg", "--cov-context=test", "--cov-report="]
    done = subprocess.run(
        [*command, f"--junitxml={reports / 'junit.xml'}"],
        cwd=root / "shop",
        capture_output=True,
        text=True,
        env=environment,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    saved = app.record_tests(
        reports / "shop.coverage",
        "shop",
        ["shop/"],
        run_root="shop",
        junit=[reports / "junit.xml"],
        reads=reports / "reads.json",
    )
    assert saved["provenance"]["status"] == "verified"
    record = json.loads(Path(saved["path"]).read_text())
    record["seconds"] = [1.0] * len(record["tests"])
    (reports / "even.json").write_text(json.dumps(record))
    return root, reports / "even.json"


def test_reach_of_past_changes_and_what_would_lower_it(recorded, capsys):
    root, record = recorded
    options = ["--suite", "shop", "--record", str(record), "--history", "commits"]
    found = reach_report.main([str(root), *options, "--then", "local:shop/pkg/settings.py"])

    # Newest first: a test, a top-level change that reaches both users' tests, a function body.
    today = [row["today"] for row in found["pull_requests"]]
    assert today == pytest.approx([1 / 3, 2 / 3, 1 / 3], abs=0.001)
    assert found["number"] | {"mean": 0, "median": 0} == {
        "mean": 0,
        "median": 0,
        "nearly_all": 0,
        "under_fifth": 0,
        "whole_suite": 0,
        "left_out": 0,
    }
    assert found["number"]["mean"] == pytest.approx(4 / 9, abs=0.001)
    layers = found["layers"]
    assert layers["ran"] == pytest.approx(2 / 9, abs=0.001)
    assert layers["top_level"] == pytest.approx(2 / 9, abs=0.001)
    assert layers["careful"] == pytest.approx(0, abs=0.001) and layers["always"] == 0
    assert found["kinds"] == {
        "source, inside functions": 1,
        "source, top level": 1,
        "test or test helper": 1,
    }

    # The file that cost most is the one whose top level changed, and the part is its folder.
    assert [(row["path"], row["changes"]) for row in found["costly"]] == [
        ("shop/pkg/settings.py", 1),
        ("shop/pkg/labels.py", 1),
    ]
    assert [(row["part"], row["files"]) for row in found["parts"]] == [("shop/pkg/", 5)]

    # Keeping that change local is the asked what-if and also the best one found.
    assert [row["mean"] for row in found["then"]] == [
        pytest.approx(4 / 9, abs=0.001),
        pytest.approx(2 / 9, abs=0.001),
    ]
    assert found["best"][-1]["mean"] == pytest.approx(2 / 9, abs=0.001)
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
