"""A small repository with a history, whose suite really ran once, for the replay scripts."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from anaxigraph import recorder
from anaxigraph.application import Application

FILES = {
    ".gitignore": "__pycache__/\n.anaxigraph-v2/\n",
    "shop/pkg/__init__.py": "",
    "shop/pkg/settings.py": "LIMIT = 2\nRATE = 4\n",
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
# Four changes after the first commit: inside a function, to a name one file uses, to a name
# nothing uses, and to a test.
LATER = [
    ("shop/pkg/labels.py", "def label(order):\n    return f\"Order {order['total']}\"\n"),
    ("shop/pkg/settings.py", "LIMIT = 3\nRATE = 4\n"),
    ("shop/pkg/settings.py", "LIMIT = 3\nRATE = 5\n"),
    (
        "shop/tests/test_labels.py",
        "from pkg.labels import label\n\n\ndef test_label():\n"
        "    assert label({'total': 5}) == 'Order 5'\n",
    ),
]


def git(root: Path, *args: str) -> None:
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    subprocess.run(["git", "-C", str(root), *identity, *args], check=True, capture_output=True)


@pytest.fixture(scope="session")
def recorded(tmp_path_factory) -> tuple[Path, Path]:
    """A checkout with five commits, and the record of its suite at the last one with every
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
