"""Tests are picked from a real recorded run, and everything is picked when it cannot tell."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from anaxigraph.application import Application
from anaxigraph.interfaces import cli

FILES = {
    "shop/pkg/__init__.py": "",
    "shop/pkg/settings.py": "LIMIT = 3\n",
    "shop/pkg/prices.py": (
        "from functools import lru_cache\n\nfrom pkg.settings import LIMIT\n\n\n"
        "def total(items):\n    return sum(items[:LIMIT])\n\n\n"
        "@lru_cache\ndef rates():\n    return {'eur': 1.0}\n"
    ),
    "shop/pkg/orders.py": (
        "from pkg.prices import total\n\n\ndef place(items):\n    return {'total': total(items)}\n"
    ),
    "shop/pkg/labels.py": "def label(order):\n    return f\"Order {order['total']}\"\n",
    "shop/pkg/unused.py": "def idle():\n    return None\n",
    "shop/pkg/rates.json": '{"eur": 1.0}\n',
    "shop/tests/conftest.py": "import pytest\n\n\n@pytest.fixture\ndef items():\n    return [1, 2]\n",
    "shop/tests/support.py": "def order():\n    return {'total': 5}\n",
    "shop/tests/test_orders.py": (
        "from pkg.orders import place\n\n\ndef test_place(items):\n    assert place(items)['total'] == 3\n"
    ),
    "shop/tests/test_prices.py": (
        "from pkg.prices import total\n\n\ndef test_total(items):\n    assert total(items) == 3\n"
    ),
    "shop/tests/test_labels.py": (
        "from pkg.labels import label\nfrom support import order\n\n\n"
        "def test_label():\n    assert label(order()) == 'Order 5'\n"
    ),
    "shop/tests/test_readme.py": (
        "from pathlib import Path\n\n\ndef test_readme():\n"
        "    assert Path(__file__).parents[2].joinpath('README.md').exists()\n"
    ),
    "README.md": "Shop.\n",
    "web/app.ts": "export const start = () => 1;\n",
}
T = "shop/tests/test_{}.py".format


@pytest.fixture(scope="module")
def recorded(tmp_path_factory) -> Path:
    """A checkout whose suite really ran once, with one coverage context per test."""
    root = tmp_path_factory.mktemp("recorded") / "checkout"
    for relative, content in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    run = [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]
    run += ["--cov=pkg", "--cov-context=test", "--cov-report=", "--junitxml=junit.xml"]
    done = subprocess.run(run, cwd=root / "shop", capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    coverage = root.parent / "shop.coverage"
    shutil.move(root / "shop/.coverage", coverage)
    shutil.move(root / "shop/junit.xml", root.parent / "junit.xml")
    saved = Application(root).record_tests(
        coverage, "shop", ["shop/"], run_root="shop", junit=root.parent / "junit.xml"
    )
    assert saved["test_files"] == 4 and saved["sources_executed"] == 3
    return root


@pytest.fixture
def checkout(recorded: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(recorded, tmp_path / "checkout"))


def picked(root: Path) -> dict:
    [suite] = Application(root).tests_for_change()["suites"]
    return suite


def test_an_unchanged_checkout_needs_no_test_and_a_missing_record_says_so(checkout, tmp_path):
    assert picked(checkout)["status"] == "nothing_to_run"
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "a.py").write_text("x = 1\n")
    assert Application(bare).tests_for_change()["status"] == "no_record"


def test_a_change_inside_a_function_picks_the_tests_that_executed_the_file(checkout):
    target = checkout / "shop/pkg/orders.py"
    target.write_text(
        target.read_text().replace("{'total': total(items)}", "dict(total=total(items))")
    )
    suite = picked(checkout)
    assert suite["status"] == "selected"
    # The test that reads no source runs whenever anything changed.
    assert suite["test_files"] == [T("orders"), T("readme")]
    assert suite["selected"] == 2 and suite["total"] == 4 and 0 < suite["time_share"] <= 1


def test_a_changed_module_level_value_also_picks_the_tests_of_its_importers(checkout):
    (checkout / "shop/pkg/settings.py").write_text("LIMIT = 1\n")
    suite = picked(checkout)
    # No test executed a line of settings.py; prices.py imports it and two tests ran prices.py.
    assert suite["test_files"] == [T("orders"), T("prices"), T("readme")]
    assert any(reason["rule"].startswith("import-time code changed") for reason in suite["because"])


def test_a_cached_function_counts_as_import_time_code(checkout):
    target = checkout / "shop/pkg/prices.py"
    target.write_text(target.read_text().replace("{'eur': 1.0}", "{'eur': 2.0}"))
    # Only a first caller runs a cached body, so its importers' tests are needed as well.
    assert T("orders") in picked(checkout)["test_files"]


def test_changed_tests_helpers_and_conftest_pick_the_tests_that_use_them(checkout):
    (checkout / T("prices")).write_text((checkout / T("prices")).read_text() + "\n# note\n")
    assert picked(checkout)["test_files"] == [T("prices"), T("readme")]
    (checkout / "shop/tests/support.py").write_text("def order():\n    return {'total': 6}\n")
    assert T("labels") in picked(checkout)["test_files"]
    (checkout / "shop/tests/conftest.py").write_text(
        (checkout / "shop/tests/conftest.py").read_text() + "\n# note\n"
    )
    assert picked(checkout)["selected"] == 4


def test_an_input_that_cannot_be_attributed_picks_everything_and_says_why(checkout):
    (checkout / "shop/pkg/rates.json").write_text('{"eur": 2.0}\n')
    suite = picked(checkout)
    assert suite["status"] == "all" and suite["selected"] == suite["total"] == 4
    assert suite["everything_because"] == [
        "shop/pkg/rates.json: an input the record cannot attribute to tests"
    ]


def test_new_modules_follow_their_importers_and_an_orphan_picks_everything(checkout):
    (checkout / "shop/pkg/discounts.py").write_text("def discount(total):\n    return total\n")
    orders = checkout / "shop/pkg/orders.py"
    orders.write_text("from pkg.discounts import discount\n" + orders.read_text())
    suite = picked(checkout)
    assert suite["status"] == "selected" and T("orders") in suite["test_files"]
    (checkout / "shop/pkg/plugin.py").write_text("def hook():\n    return 1\n")
    suite = picked(checkout)
    assert suite["status"] == "all"
    assert "nothing imports it" in suite["everything_because"][0]


def test_prose_other_projects_and_unloaded_modules_pick_no_source_tests(checkout):
    (checkout / "README.md").write_text("Shop, renamed.\n")
    (checkout / "web/app.ts").write_text("export const start = () => 2;\n")
    (checkout / "shop/pkg/unused.py").write_text("def idle():\n    return 0\n")
    suite = picked(checkout)
    assert suite["test_files"] == [T("readme")]
    assert {
        "path": "shop/pkg/unused.py",
        "rule": "not loaded by the recorded suite",
        "tests": 0,
    } in (suite["because"])


def test_validation_and_the_command_line_carry_the_same_answer(checkout, capsys):
    target = checkout / "shop/pkg/labels.py"
    target.write_text(target.read_text().replace("Order", "Order no."))
    validated = Application(checkout).validate_change()["tests"]
    assert validated["suites"][0]["test_files"] == [T("labels"), T("readme")]
    assert cli.main(["tests", "--repo", str(checkout), "--paths"]) == 0
    assert capsys.readouterr().out.split() == [T("labels"), T("readme")]
    (checkout / "shop/pkg/rates.json").write_text("{}\n")
    assert cli.main(["tests", "--repo", str(checkout), "--paths"]) == 0
    assert capsys.readouterr().out.strip() == ""  # Nothing to list: the whole suite is needed.
