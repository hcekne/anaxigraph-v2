"""Tests are picked from a real recorded run, and everything is picked when it cannot tell."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from anaxigraph import recorder
from anaxigraph.application import Application
from anaxigraph.interfaces import cli

READ = "import json\nfrom pathlib import Path\n\n"
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
    # Run by tests that otherwise only read values, so that the record names an input of theirs.
    "shop/pkg/text.py": "def same(value):\n    return value\n",
    "shop/pkg/unused.py": "def idle():\n    return None\n",
    "shop/pkg/widget.js": "export const size = 1;\n",
    # Never loaded: one where coverage measures, one where it does not.
    "shop/pkg/spare.py": "def spare():\n    return None\n",
    "shop/tools.py": "def tool():\n    return 1\n",
    # Loaded the way a migration tool does it, from a folder coverage does not measure.
    "shop/pkg/schema.py": "VERSION = 1\n",
    "shop/migrations/step_one.py": "from pkg.schema import VERSION\n\nSTEP = VERSION\n",
    "shop/tests/test_migrate.py": (
        "import importlib.util\nfrom pathlib import Path\n\n\ndef test_migrate():\n"
        "    for path in sorted(Path(__file__).parents[1].joinpath('migrations').glob('*.py')):\n"
        "        spec = importlib.util.spec_from_file_location(path.stem, path)\n"
        "        module = importlib.util.module_from_spec(spec)\n"
        "        spec.loader.exec_module(module)\n"
        "        assert module.STEP == 1\n"
    ),
    "shop/pkg/rates.json": '{"eur": 1.0}\n',
    # A function that runs when its module is imported, and a module that only reads the result.
    "shop/pkg/catalog.py": "def _build():\n    return {'book': 4}\n\n\nTABLE = _build()\n",
    "shop/pkg/shelf.py": (
        "from pkg import catalog\n\n\ndef price(name):\n    return catalog.TABLE[name]\n"
    ),
    # A value one module derives from another when it is imported.
    "shop/pkg/base.py": "class Base:\n    kind = 'plain'\n",
    "shop/pkg/items.py": (
        "from pkg.base import Base\n\n\nclass Item(Base):\n    pass\n\n\n"
        "def make():\n    return Item()\n"
    ),
    # Data read by a function, data read at import, and a folder scanned for plug-ins.
    "shop/pkg/taxes.json": '{"vat": 0.2}\n',
    "shop/pkg/taxes.py": (
        READ + "\ndef vat():\n"
        "    return json.loads(Path(__file__).with_name('taxes.json').read_text())['vat']\n"
    ),
    "shop/pkg/regions.json": '["eu"]\n',
    "shop/pkg/regions.py": (
        READ + "REGIONS = json.loads(Path(__file__).with_name('regions.json').read_text())\n"
    ),
    # Data read once and kept: only the first of its two tests opens the file.
    "shop/pkg/fees.json": '{"card": 2, "cash": 0}\n',
    "shop/pkg/fees.py": (
        "import json\nfrom functools import lru_cache\nfrom pathlib import Path\n\n\n"
        "@lru_cache\ndef _table():\n"
        "    return json.loads(Path(__file__).with_name('fees.json').read_text())\n\n\n"
        "def fee(kind):\n    return _table()[kind]\n"
    ),
    "shop/tests/test_fees_card.py": (
        "from pkg.fees import fee\n\n\ndef test_card():\n    assert fee('card') == 2\n"
    ),
    "shop/tests/test_fees_cash.py": (
        "from pkg.fees import fee\n\n\ndef test_cash():\n    assert fee('cash') == 0\n"
    ),
    "shop/pkg/plugins/__init__.py": "",
    "shop/pkg/plugins/alpha.py": "NAME = 'alpha'\n",
    "shop/pkg/loader.py": (
        "import importlib\nfrom pathlib import Path\n\n\ndef names():\n"
        "    found = sorted(path.stem for path in Path(__file__).with_name('plugins').glob('*.py'))\n"
        "    found = [name for name in found if name != '__init__']\n"
        "    return [importlib.import_module(f'pkg.plugins.{name}').NAME for name in found]\n"
    ),
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
    "shop/tests/test_shelf.py": (
        "from pkg.shelf import price\n\n\ndef test_price():\n    assert price('book') == 4\n"
    ),
    "shop/tests/test_items.py": (
        "from pkg.items import make\n\n\ndef test_make():\n    assert make() is not None\n"
    ),
    "shop/tests/test_kinds.py": (
        "from pkg.items import Item\nfrom pkg.text import same\n\n\n"
        "def test_kind():\n    assert same(Item.kind) == 'plain'\n"
    ),
    "shop/tests/test_taxes.py": (
        "from pkg.taxes import vat\n\n\ndef test_vat():\n    assert vat() == 0.2\n"
    ),
    "shop/tests/test_regions.py": (
        "from pkg.regions import REGIONS\nfrom pkg.text import same\n\n\n"
        "def test_regions():\n    assert same(REGIONS) == ['eu']\n"
    ),
    "shop/tests/test_loader.py": (
        "from pkg.loader import names\n\n\ndef test_names():\n    assert names() == ['alpha']\n"
    ),
    # One test starts a process that could read the checkout; the other runs git elsewhere.
    "shop/tests/test_tool.py": (
        "import subprocess\nimport sys\n\nfrom pkg.labels import label\n\n\ndef test_tool():\n"
        "    done = subprocess.run([sys.executable, '-c', 'print(5)'], capture_output=True)\n"
        "    assert label({'total': int(done.stdout)}) == 'Order 5'\n"
    ),
    "shop/tests/test_git.py": (
        "import subprocess\n\nfrom pkg.unused import idle\n\n\ndef test_git(tmp_path):\n"
        "    subprocess.run(['git', '--version'], cwd=tmp_path, check=True, capture_output=True)\n"
        "    subprocess.run(['git', '-C', str(tmp_path), '--version'], check=True)\n"
        # Started in the checkout, yet none of these reads a file of it.
        "    subprocess.run(['git', '-c', 'a.b=c', 'init', '-q', str(tmp_path / 'r')], check=True)\n"
        "    there = f'--git-dir={tmp_path}/r/.git'\n"
        "    subprocess.run(['git', there, 'symbolic-ref', 'HEAD'], capture_output=True)\n"
        "    subprocess.run(['git', 'rev-parse', '--git-dir'], capture_output=True)\n"
        "    assert idle() is None\n"
    ),
    # These two do read the checkout: its tracked files, and one named through an option.
    "shop/tests/test_tracked.py": (
        "import subprocess\nfrom pathlib import Path\n\nfrom pkg.text import same\n\n\n"
        "def test_tracked(tmp_path):\n"
        "    subprocess.run(['git', 'ls-files'], capture_output=True)\n"
        "    assert same(1) == 1\n\n\n"
        "def test_named(tmp_path):\n"
        "    here = f'--git-dir={Path(__file__).parents[2]}/.git'\n"
        "    subprocess.run(['git', here, 'log'], cwd=tmp_path, capture_output=True)\n"
    ),
    # A test file that another test file imports from.
    "shop/tests/test_shared.py": (
        "from pkg.text import same\n\nLIMIT = 5\n\n\n"
        "def test_limit():\n    assert same(LIMIT) == 5\n"
    ),
    "shop/tests/test_borrow.py": (
        "from pkg.text import same\nfrom test_shared import LIMIT\n\n\n"
        "def test_borrow():\n    assert same(LIMIT) == 5\n"
    ),
    # A dependency list that a test checks, and a binary file that a test reads.
    "shop/requirements.txt": "pytest\n",
    "shop/tests/test_pins.py": (
        "from pathlib import Path\n\n\ndef test_pins():\n"
        "    assert 'pytest' in Path(__file__).parents[1].joinpath('requirements.txt').read_text()\n"
    ),
    "shop/tests/logo.bin": "\x00\x01logo",
    "shop/tests/test_logo.py": (
        "from pathlib import Path\n\n\ndef test_logo():\n"
        "    assert Path(__file__).with_name('logo.bin').read_bytes().endswith(b'logo')\n"
    ),
    "shop/notes.md": "Read by a test.\n",
    # It also warns when imported, so that pytest reads the file to print the warning's line.
    "shop/tests/test_notes.py": (
        "import warnings\nfrom pathlib import Path\n\n"
        "warnings.warn('soon', DeprecationWarning, stacklevel=1)\n\n\ndef test_notes():\n"
        "    assert Path(__file__).parents[1].joinpath('notes.md').read_text()\n"
    ),
    "README.md": "Shop.\n",
    # The suite is started from a file of the checkout, as a .venv/bin/pytest script would be.
    "shop/run.py": "import sys\n\nimport pytest\n\nsys.exit(pytest.main(sys.argv[1:]))\n",
    "web/app.ts": "export const start = () => 1;\n",
    "web/routes.json": '["/"]\n',
    "shop/tests/test_routes.py": (
        "import json\nfrom pathlib import Path\n\n\ndef test_routes():\n"
        "    routes = Path(__file__).parents[2] / 'web' / 'routes.json'\n"
        "    assert json.loads(routes.read_text()) == ['/']\n"
    ),
}
T = "shop/tests/test_{}.py".format
TOTAL = 22  # Test files in the fixture.
# Selected whenever anything changed: no recorded input, and a process the record cannot see.
ALWAYS = [T("readme"), T("tool"), T("tracked")]


def chosen(*names: str) -> list[str]:
    return sorted({*ALWAYS, *(T(name) for name in names)})


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> Path:
    """A checkout whose suite really ran once, with one coverage context per test and the
    reads recorder; the run's reports lie beside it."""
    root = tmp_path_factory.mktemp("recorded") / "checkout"
    for relative, content in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    manifest = Application(root).start_recording("shop")["manifest"]
    command = [sys.executable, "run.py", "tests", "-q", "-p", "no:cacheprovider"]
    command += ["--cov=pkg", "--cov-context=test", "--cov-report=", "--junitxml=junit.xml"]
    environment = os.environ | {
        "PYTHONPATH": str(Path(recorder.__file__).parent),
        "ANAXIGRAPH_ROOT": str(root),  # The fixture is not a git checkout.
        "ANAXIGRAPH_READS": str(root.parent / "reads.json"),
        "ANAXIGRAPH_RECORDING": manifest,
        "ANAXIGRAPH_TRACE_CHILDREN": "0",  # This fixture deliberately records opaque starts.
        "COVERAGE_FILE": str(root / "shop/.coverage"),
    }
    done = subprocess.run(
        [*command, "-p", "anaxigraph_reads"],
        cwd=root / "shop",
        capture_output=True,
        text=True,
        env=environment,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    shutil.move(root / "shop/.coverage", root.parent / "shop.coverage")
    shutil.move(root / "shop/junit.xml", root.parent / "junit.xml")
    return root


def record(root: Path, run: Path, reads: bool, reports: Path | None = None) -> dict:
    """Save the record of the run at `run` in a copy of that checkout."""
    reports = reports or run.parent
    return Application(root).record_tests(
        reports / "shop.coverage",
        "shop",
        ["shop/"],
        run_root="shop",
        recorded_root=str(run),  # The suite ran in the original, not in the copy.
        junit=[reports / "junit.xml"],
        reads=reports / "reads.json" if reads else None,
    )


@pytest.fixture
def checkout(run: Path, tmp_path: Path) -> Path:
    """A copy of the checkout with the record of everything the run executed and read."""
    root = Path(shutil.copytree(run, tmp_path / "checkout"))
    saved = record(root, run, reads=True)
    assert saved["test_files"] == TOTAL and saved["reads_recorded"] is True
    return root


@pytest.fixture
def unread(run: Path, tmp_path: Path) -> Path:
    """The same checkout, recorded from coverage alone."""
    root = Path(shutil.copytree(run, tmp_path / "unread"))
    assert record(root, run, reads=False)["reads_recorded"] is False
    return root


def picked(root: Path) -> dict:
    [suite] = Application(root).tests_for_change()["suites"]
    return suite


@pytest.mark.parametrize("edit_at", ["statistics", "record-read"])
def test_validation_uses_one_snapshot_with_real_recorded_consumers(checkout, monkeypatch, edit_at):
    from anaxigraph.application import selection

    app = Application(checkout)
    baseline = app.prepare_change("Improve prices")["baseline_id"]
    receipts = [{"command": "reported fixture check", "snapshot_id": baseline, "exit_code": 0}]
    target = app.store if edit_at == "statistics" else selection
    method = "stats" if edit_at == "statistics" else "_records"
    original = getattr(target, method)
    edited = []

    def edit(*args, **kwargs):
        result = original(*args, **kwargs)
        if not edited:
            edited.append(True)
            path = checkout / "shop/pkg/prices.py"
            path.write_text(path.read_text() + "\nnew_setting = 1\n")
        return result

    monkeypatch.setattr(target, method, edit)
    result = app.validate_change(receipts)
    assert result["tests"]["snapshot_id"] == result["candidate_snapshot_id"] == baseline
    assert result["completion_allowed"] is False
    assert result["checkout_current"] is False
    assert result["validation_status"] == "incomplete"


def test_an_unchanged_checkout_needs_no_test_and_a_missing_record_says_so(checkout, tmp_path):
    assert picked(checkout)["status"] == "nothing_to_run"
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "a.py").write_text("x = 1\n")
    assert Application(bare).tests_for_change()["status"] == "no_record"


@pytest.mark.parametrize(
    "scope", [["./"], ["./shop/"], ["/shop/"], ["../shop/"], ["shop//"], ["shop/../"], ["shop/./"]]
)
def test_legacy_unmatched_scope_requires_full_checks_after_an_edit(checkout, scope):
    path = checkout / ".anaxigraph-v2/test-records/shop.json"
    record = json.loads(path.read_text())
    record["scope"] = scope
    path.write_text(json.dumps(record))
    (checkout / "shop/pkg/settings.py").write_text("LIMIT = 4\n")
    result = picked(checkout)
    assert result["status"] == "all"
    assert result["selected"] == result["total"] == TOTAL
    assert any("scope is not normalized" in reason for reason in result["everything_because"])


def test_a_change_inside_a_function_picks_the_tests_that_executed_the_file(checkout):
    target = checkout / "shop/pkg/orders.py"
    target.write_text(
        target.read_text().replace("{'total': total(items)}", "dict(total=total(items))")
    )
    suite = picked(checkout)
    assert suite["status"] == "selected"
    # With them the tests whose inputs the record cannot name, as for every change.
    assert suite["test_files"] == chosen("orders")
    assert suite["selected"] == 4 and suite["total"] == TOTAL and 0 < suite["time_share"] <= 1
    rules = {reason["rule"] for reason in suite["because"]}
    assert {"tests with no recorded input", "tests that started another process"} <= rules


def test_a_changed_module_level_value_also_picks_the_tests_of_its_importers(checkout):
    (checkout / "shop/pkg/settings.py").write_text("LIMIT = 1\n")
    suite = picked(checkout)
    # No test executed a line of settings.py; prices.py imports it and two tests ran prices.py.
    assert suite["test_files"] == chosen("orders", "prices")
    assert any(reason["rule"].startswith("import-time code changed") for reason in suite["because"])


def test_a_cached_function_counts_as_import_time_code(checkout):
    target = checkout / "shop/pkg/prices.py"
    target.write_text(target.read_text().replace("{'eur': 1.0}", "{'eur': 2.0}"))
    # Only a first caller runs a cached body, so its importers' tests are needed as well.
    assert T("orders") in picked(checkout)["test_files"]


def test_a_function_that_ran_at_import_counts_as_import_time_code(checkout):
    target = checkout / "shop/pkg/catalog.py"
    target.write_text(target.read_text().replace("'book': 4", "'book': 5"))
    # _build ran while catalog.py was imported, outside any test. The test of shelf.py reads
    # the table it built and would fail, though it never ran a line of catalog.py.
    assert picked(checkout)["test_files"] == chosen("shelf")


def test_what_a_module_derives_at_import_reaches_the_tests_of_its_users(checkout):
    (checkout / "shop/pkg/base.py").write_text("class Base:\n    kind = 'fancy'\n")
    # items.py subclasses Base when imported. One test ran items.py; the other only reads the
    # inherited value and would fail without running a line of either file.
    assert picked(checkout)["test_files"] == chosen("items", "kinds")


def test_changed_tests_helpers_and_conftest_pick_the_tests_that_use_them(checkout):
    (checkout / T("prices")).write_text((checkout / T("prices")).read_text() + "\n# note\n")
    assert picked(checkout)["test_files"] == chosen("prices")
    # A test file that another one imports from brings that one along.
    (checkout / T("shared")).write_text(FILES[T("shared")].replace("5", "6"))
    assert picked(checkout)["test_files"] == chosen("prices", "shared", "borrow")
    (checkout / "shop/tests/support.py").write_text("def order():\n    return {'total': 6}\n")
    assert T("labels") in picked(checkout)["test_files"]
    (checkout / "shop/tests/conftest.py").write_text(
        (checkout / "shop/tests/conftest.py").read_text() + "\n# note\n"
    )
    assert picked(checkout)["selected"] == TOTAL


def test_data_is_attributed_to_the_tests_and_modules_that_read_it(checkout):
    (checkout / "shop/pkg/taxes.json").write_text('{"vat": 0.25}\n')
    suite = picked(checkout)
    assert suite["status"] == "selected" and suite["test_files"] == chosen("taxes")
    (checkout / "shop/pkg/taxes.json").write_text('{"vat": 0.2}\n')
    # Read while regions.py was imported: whoever uses that module sees the data.
    (checkout / "shop/pkg/regions.json").write_text('["eu", "us"]\n')
    suite = picked(checkout)
    assert suite["test_files"] == chosen("regions")
    assert "shop/pkg/regions.py read it when imported: its tests" in {
        reason["rule"] for reason in suite["because"]
    }


def test_data_kept_after_one_read_reaches_every_test_that_ran_the_reader(checkout):
    (checkout / "shop/pkg/fees.json").write_text('{"card": 3, "cash": 1}\n')
    suite = picked(checkout)
    # Only test_fees_card.py opened the file; test_fees_cash.py got the kept copy and would
    # fail as well. Both ran fees.py, the code that read it.
    assert suite["test_files"] == chosen("fees_card", "fees_cash")


def test_reads_reach_prose_and_files_outside_the_scope(checkout):
    (checkout / "shop/notes.md").write_text("Read by a test, and changed.\n")
    assert picked(checkout)["test_files"] == chosen("notes")
    (checkout / "shop/notes.md").write_text(FILES["shop/notes.md"])
    (checkout / "web/routes.json").write_text('["/", "/new"]\n')
    assert picked(checkout)["test_files"] == chosen("routes")


def test_a_file_the_run_read_only_to_print_a_warning_is_not_an_input_of_every_test(checkout):
    target = checkout / T("notes")
    target.write_text(target.read_text() + "\n# note\n")
    assert picked(checkout)["test_files"] == chosen("notes")


def test_a_file_appearing_in_a_scanned_folder_picks_the_tests_that_scanned_it(checkout):
    (checkout / "shop/pkg/plugins/beta.py").write_text("NAME = 'beta'\n")
    suite = picked(checkout)
    # Nothing imports the new module, but the loader's test scanned its folder.
    assert suite["status"] == "selected" and suite["test_files"] == chosen("loader")


def test_a_removed_module_picks_everything(checkout):
    (checkout / "shop/pkg/unused.py").unlink()
    # test_git.py still imports it and would fail without having run a line of it in a test
    # of its own; which files still import a removed one, the record cannot say.
    suite = picked(checkout)
    assert suite["status"] == "all"
    assert suite["everything_because"] == [
        "shop/pkg/unused.py: removed, and what still imports it is not recorded"
    ]


def test_a_test_that_started_a_process_always_runs_but_git_elsewhere_does_not_count(checkout):
    (checkout / "shop/pkg/settings.py").write_text("LIMIT = 2\n")
    suite = picked(checkout)
    assert T("tool") in suite["test_files"] and T("git") not in suite["test_files"]


def test_without_recorded_reads_a_data_file_picks_everything(unread):
    (unread / "shop/pkg/taxes.json").write_text('{"vat": 0.25}\n')
    suite = picked(unread)
    assert suite["status"] == "all" and suite["selected"] == suite["total"] == TOTAL
    assert (
        "shop/pkg/taxes.json: an input the record cannot attribute to tests"
        in suite["everything_because"]
    )
    assert suite["reads_recorded"] is False and "reads a file as data" in suite["assurance"]
    (unread / "shop/pkg/taxes.json").write_text('{"vat": 0.2}\n')
    (unread / "web/routes.json").write_text("[]\n")
    assert picked(unread)["status"] == "all"  # Unbound evidence cannot rule out shared inputs.


def test_source_in_another_language_is_ruled_out_only_by_recorded_reads(checkout, unread):
    for root in (checkout, unread):
        (root / "shop/pkg/widget.js").write_text("export const size = 2;\n")
    # The suite cannot import it, and the reads show that no test opened it either.
    assert picked(checkout)["test_files"] == chosen()
    assert (
        "shop/pkg/widget.js: an input the record cannot attribute to tests"
        in picked(unread)["everything_because"]
    )


def test_a_dependency_list_picks_everything_though_a_test_reads_it(checkout):
    (checkout / "shop/requirements.txt").write_text("pytest\nrequests\n")
    suite = picked(checkout)
    # test_pins.py read the file, but every test runs on what was installed from it.
    assert suite["status"] == "all"
    assert suite["everything_because"] == ["shop/requirements.txt: the suite is installed from it"]


@pytest.mark.parametrize(
    "name",
    [
        "uv.lock",
        "requirements-dev.txt",
        "constraints.txt",
        "pyproject.toml",
        ".github/workflows/ci.yml",
        ".github/actions/setup/action.yml",
    ],
)
@pytest.mark.parametrize("fixture", ["checkout", "unread"])
def test_shared_setup_outside_the_scope_cannot_be_ignored(request, fixture, name):
    root = request.getfixturevalue(fixture)
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# changed shared setup\n")
    suite = picked(root)
    assert suite["status"] == "all"
    assert any(name in reason for reason in suite["everything_because"])


def test_an_ancestor_dependency_list_widens_nested_scopes(checkout, run):
    app = Application(checkout)
    app.record_tests(
        run.parent / "shop.coverage",
        "shop",
        ["shop/pkg", "shop/tests"],
        run_root="shop",
        recorded_root=str(run),
        junit=[run.parent / "junit.xml"],
        reads=run.parent / "reads.json",
    )
    (checkout / "shop/requirements.txt").write_text("pytest\nrequests\n")
    assert picked(checkout)["status"] == "all"


def test_another_project_dependency_list_does_not_widen_this_suite(checkout):
    (checkout / "web/requirements.txt").write_text("a-frontend-tool\n")
    assert picked(checkout)["test_files"] == chosen()


def test_a_binary_file_is_compared_like_any_other_input(checkout):
    (checkout / "shop/tests/logo.bin").write_bytes(b"\x00\x02logo")
    assert picked(checkout)["test_files"] == chosen("logo")


def test_an_input_nothing_read_picks_everything_and_says_why(checkout):
    (checkout / "shop/pkg/rates.json").write_text('{"eur": 2.0}\n')
    suite = picked(checkout)
    assert suite["status"] == "all" and suite["selected"] == suite["total"] == TOTAL
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
    # Coverage lists spare.py without a line; the recorder saw nothing load tools.py.
    (checkout / "shop/pkg/spare.py").write_text("def spare():\n    return 0\n")
    (checkout / "shop/tools.py").write_text("def tool():\n    return 2\n")
    suite = picked(checkout)
    # unused.py is run by the test that also runs git; that is the only addition.
    assert suite["test_files"] == chosen("git")
    rules = {reason["path"]: reason["rule"] for reason in suite["because"]}
    assert (
        rules["shop/pkg/spare.py"] == rules["shop/tools.py"] == "not loaded by the recorded suite"
    )


def test_a_file_coverage_did_not_measure_is_not_taken_for_unloaded(checkout, unread):
    for root in (checkout, unread):
        # Loaded by a test, by path, from a folder outside what coverage measured.
        (root / "shop/migrations/step_one.py").write_text("STEP = 1\n")
        suite = picked(root)
        assert suite["status"] == "all", root
        assert (
            "shop/migrations/step_one.py: new, or not measured when recorded, and nothing imports it"
            in suite["everything_because"]
        )
    # Coverage alone cannot tell a file nobody loaded from one it was not measuring.
    (unread / "shop/migrations/step_one.py").write_text(FILES["shop/migrations/step_one.py"])
    (unread / "shop/tools.py").write_text("def tool():\n    return 2\n")
    assert "shop/tools.py: new, or not measured" in picked(unread)["everything_because"][0]


def test_an_empty_file_that_gains_code_is_not_taken_for_unloaded(checkout, unread):
    for root in (checkout, unread):
        # Importing an empty file may leave no line behind, so that coverage lists it like
        # a file nobody loaded; which of the two it was depends on the Python version.
        (root / "shop/pkg/__init__.py").write_text("VERSION = 1\n")
        reason = picked(root)["everything_because"][0]
        assert reason.startswith("shop/pkg/__init__.py: ")
        assert reason.endswith(", and nothing imports it")


def test_a_walk_that_ends_where_no_import_leads_picks_everything(checkout):
    (checkout / "shop/pkg/schema.py").write_text("VERSION = 2\n")
    suite = picked(checkout)
    # step_one.py uses the value and is loaded by path; its test did run schema.py, but
    # nothing shows that it is the only one loading files from that folder.
    assert suite["status"] == "all"
    assert suite["everything_because"] == [
        "shop/pkg/schema.py: import-time code changed; shop/migrations/step_one.py uses it, "
        "and no import leads to that"
    ]


def test_new_test_files_are_picked_under_either_naming_and_listed_with_everything(checkout):
    (checkout / "shop/tests/extra_test.py").write_text("def test_extra():\n    assert True\n")
    suite = picked(checkout)
    assert suite["test_files"] == sorted([*ALWAYS, "shop/tests/extra_test.py"])
    assert suite["total"] == TOTAL + 1
    (checkout / "shop/pkg/rates.json").write_text("{}\n")
    suite = picked(checkout)
    assert suite["status"] == "all" and "shop/tests/extra_test.py" in suite["test_files"]
    assert suite["selected"] == suite["total"] == TOTAL + 1


def test_a_new_file_in_a_folder_a_test_scanned_picks_that_test(checkout):
    (checkout / "shop/migrations/step_two.py").write_text("STEP = 1\n")
    suite = picked(checkout)
    assert suite["status"] == "selected" and suite["test_files"] == chosen("migrate")


def test_validation_and_the_command_line_carry_the_same_answer(checkout, capsys):
    target = checkout / "shop/pkg/labels.py"
    target.write_text(target.read_text().replace("Order", "Order no."))
    validated = Application(checkout).validate_change()["tests"]
    assert validated["suites"][0]["test_files"] == chosen("labels")
    assert cli.main(["tests", "--repo", str(checkout), "--paths"]) == 0
    assert capsys.readouterr().out.split() == chosen("labels")
    (checkout / "shop/pkg/rates.json").write_text("{}\n")
    assert cli.main(["tests", "--repo", str(checkout), "--paths"]) == 0
    assert capsys.readouterr().out.strip() == ""  # Nothing to list: the whole suite is needed.
    bare = checkout.parent / "bare"
    bare.mkdir()
    assert cli.main(["tests", "--repo", str(bare), "--recorder"]) == 0
    assert (Path(capsys.readouterr().out.strip()) / "anaxigraph_reads.py").is_file()
    assert not list(bare.iterdir())  # Asked from a test setup: it leaves nothing behind.


def test_a_report_from_another_checkout_is_refused(run, tmp_path):
    root = Path(shutil.copytree(run, tmp_path / "checkout"))
    reports = Path(
        shutil.copytree(run.parent, tmp_path / "reports", ignore=lambda *_: ["checkout"])
    )
    report = reports / "reads.json"
    report.write_text(report.read_text().replace("shop/tests/", "elsewhere/"))
    with pytest.raises(ValueError, match="Recording source differs"):
        record(root, run, reads=True, reports=reports)
    report.write_text("{}")
    with pytest.raises(ValueError, match="Not a reads report"):
        record(root, run, reads=True, reports=reports)


def test_worker_reports_are_merged(run, tmp_path):
    import json

    root = Path(shutil.copytree(run, tmp_path / "checkout"))
    reports = Path(
        shutil.copytree(run.parent, tmp_path / "reports", ignore=lambda *_: ["checkout"])
    )
    whole = json.loads((reports / "reads.json").read_text())
    # As under pytest-xdist: the folder scans in one worker's file, the rest in another's.
    (reports / "reads.json.gw1").write_text(
        json.dumps(whole | {"opened": {}, "started": {}, "worker": "gw1"})
    )
    (reports / "reads.json").write_text(json.dumps(whole | {"listed": {}, "workers": ["gw1"]}))
    record(root, run, reads=True, reports=reports)
    (root / "shop/pkg/plugins/beta.py").write_text("NAME = 'beta'\n")
    assert picked(root)["test_files"] == chosen("loader")


def test_the_recorder_finds_the_checkout_and_assumes_the_most_of_what_it_cannot_read(tmp_path):
    (tmp_path / "checkout/.git").mkdir(parents=True)
    (tmp_path / "checkout/inner/deep").mkdir(parents=True)
    script = (
        "import anaxigraph_reads as plugin\nr = plugin.audit\n"
        "r._audit('subprocess.Popen', ())\n"  # Not the shape a real event has.
        "print(r._ROOT)\nprint(r._SEEN['started'])\n"
    )
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ANAXIGRAPH_")
    } | {"PYTHONPATH": str(Path(recorder.__file__).parent)}
    done = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path / "checkout/inner/deep",
        capture_output=True,
        text=True,
        env=environment,
    )
    assert done.returncode == 0, done.stderr
    found, started = done.stdout.splitlines()
    assert Path(found) == (tmp_path / "checkout").resolve()
    assert started == "{'': {'@'}}"  # Counted as a process started, outside any test.


def test_a_whole_suite_run_shows_what_the_selection_would_have_left_out(checkout, run, capsys):
    import json

    case = '<testcase classname="tests.test_{0}" name="t" time="1">{1}</testcase>'
    report = checkout.parent / "ran.xml"
    failing = [case.format(name, "<failure/>") for name in ("orders", "taxes")]
    report.write_text(f"<testsuite>{''.join(failing)}{case.format('prices', '')}</testsuite>")
    target = checkout / "shop/pkg/orders.py"
    target.write_text(target.read_text().replace("{'total': total(items)}", "{'total': 0}"))
    assert cli.main(["tests", "--repo", str(checkout), "--ran", str(report)]) == 0
    [suite] = json.loads(capsys.readouterr().out)["suites"]
    # The selection for orders.py holds its failing test; the failure in test_taxes.py is one
    # that it would not have run.
    assert {
        key: suite["shadow"][key]
        for key in ("failing_test_files", "failing_when_recorded", "caught", "missed")
    } == {
        "failing_test_files": 2,
        "failing_when_recorded": 0,
        "caught": 1,
        "missed": [T("taxes")],
    }
    assert suite["shadow"]["evidence_status"] == "incomplete"
    assert T("loader") in suite["shadow"]["missing_recorded_test_files"]


def test_an_unmatched_junit_case_is_not_silently_treated_as_no_failures(checkout):
    report = checkout.parent / "unknown.xml"
    report.write_text(
        '<testsuite><testcase classname="elsewhere.test_unknown" name="broken"><failure/></testcase></testsuite>'
    )
    with pytest.raises(ValueError, match="cannot be matched"):
        Application(checkout).tests_for_change(ran=[report])


def test_inspection_exposes_paged_runtime_consumers_and_marks_source_and_test_changes(checkout):
    app = Application(checkout)
    path = "shop/pkg/fees.py"
    [first] = app.inspect("module:" + path, limit=1)["test_observations"]
    [second] = app.inspect("module:" + path, limit=1, offset=1)["test_observations"]
    assert first["source_matches"] is True and first["changed_test_files"] == 0
    assert first["executing_test_files"] == first["total"] == 2
    assert first["next_offset"] == 1 and second["next_offset"] is None
    assert first["test_files"] + second["test_files"] == [T("fees_card"), T("fees_cash")]
    assert first["provenance"]["status"] == "verified"
    assert 0 <= first["recorded_duration_share"] <= 1
    (checkout / path).write_text((checkout / path).read_text() + "\n# implementation edit\n")
    (checkout / T("fees_cash")).write_text(
        (checkout / T("fees_cash")).read_text() + "\n# test edit\n"
    )
    [changed] = app.inspect("module:" + path)["test_observations"]
    assert changed["source_matches"] is False and changed["changed_test_files"] == 1
    assert changed["total"] == 2  # Retain baseline facts rather than calling them fresh coverage.


@pytest.mark.parametrize("reader", ["@", "@shop/pkg/regions.py", "+missing.py"])
def test_unattributed_processes_survive_recording_and_force_the_whole_suite(run, tmp_path, reader):
    import json

    root = Path(shutil.copytree(run, tmp_path / "checkout"))
    reports = Path(
        shutil.copytree(run.parent, tmp_path / "reports", ignore=lambda *_: ["checkout"])
    )
    path = reports / "reads.json"
    report = json.loads(path.read_text())
    report["started"].setdefault("", []).append(reader)
    path.write_text(json.dumps(report))
    record(root, run, reads=True, reports=reports)
    (root / "shop/pkg/orders.py").write_text("def place(items):\n    return {'total': 0}\n")
    suite = picked(root)
    assert suite["status"] == "all"
    assert "A process started outside an attributable test" in suite["everything_because"]


def test_process_helpers_keep_all_their_observed_consumers_without_forcing_other_tests(
    run, tmp_path
):
    import json

    root = Path(shutil.copytree(run, tmp_path / "checkout"))
    reports = Path(
        shutil.copytree(run.parent, tmp_path / "reports", ignore=lambda *_: ["checkout"])
    )
    path = reports / "reads.json"
    report = json.loads(path.read_text())
    report["started"].setdefault("", []).append("+shop/pkg/prices.py")
    path.write_text(json.dumps(report))
    record(root, run, reads=True, reports=reports)
    (root / "shop/pkg/labels.py").write_text("def label(order):\n    return ''\n")
    suite = picked(root)
    assert suite["status"] == "selected"
    assert suite["test_files"] == chosen("labels", "orders", "prices")


@pytest.mark.parametrize("legacy", [False, True])
def test_an_existing_failure_does_not_hide_another_failure_in_the_same_file(checkout, legacy):
    import json

    path = checkout / ".anaxigraph-v2/test-records/shop.json"
    saved = json.loads(path.read_text())
    saved["failed"] = [saved["tests"].index(T("taxes"))]
    saved["failed_cases"] = {T("taxes"): ["tests.test_taxes::already_broken"]}
    if legacy:
        saved.pop("failed_cases")
    path.write_text(json.dumps(saved))
    report = checkout.parent / "ran.xml"
    report.write_text(
        '<testsuite><testcase classname="tests.test_taxes" name="already_broken">'
        '<failure/></testcase><testcase classname="tests.test_taxes" name="newly_broken">'
        "<failure/></testcase></testsuite>"
    )
    (checkout / "shop/pkg/orders.py").write_text("def place(items):\n    return {'total': 0}\n")
    [suite] = Application(checkout).tests_for_change(ran=[report])["suites"]
    assert suite["shadow"]["missed"] == [T("taxes")]
    assert suite["shadow"]["failing_when_recorded"] == (0 if legacy else 1)


@pytest.mark.parametrize(
    "problem",
    [
        "source",
        "coverage",
        "junit",
        "incomplete",
        "run_id",
        "worker_missing",
        "worker_run",
        "worker_session",
        "worker_incomplete",
        "worker_format",
    ],
)
def test_invalid_recording_cannot_replace_the_last_good_record(checkout, run, tmp_path, problem):
    import json

    saved = checkout / ".anaxigraph-v2/test-records/shop.json"
    before = saved.read_bytes()
    reports = Path(
        shutil.copytree(run.parent, tmp_path / "reports", ignore=lambda *_: ["checkout"])
    )
    path = reports / "reads.json"
    report = json.loads(path.read_text())
    if problem == "source":
        (checkout / "shop/pkg/orders.py").write_text("def place(items):\n    return None\n")
    elif problem in {"coverage", "junit"}:
        target = reports / ("shop.coverage" if problem == "coverage" else "junit.xml")
        target.write_bytes(target.read_bytes() + b"\n")
    elif problem == "incomplete":
        report["complete"] = False
    elif problem == "run_id":
        report["run_id"] = "another-run"
    else:
        report["workers"] = ["gw0"]
        worker = dict(report, worker="gw0")
        if problem == "worker_run":
            worker["run_id"] = "another-run"
        elif problem == "worker_session":
            worker["session_id"] = "another-invocation"
        elif problem == "worker_incomplete":
            worker["complete"] = False
        elif problem == "worker_format":
            worker["format"] = "anaxigraph-reads-v1"
        if problem != "worker_missing":
            (reports / "reads.json.gw0").write_text(json.dumps(worker))
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        record(checkout, run, reads=True, reports=reports)
    assert saved.read_bytes() == before


@pytest.mark.parametrize("observations", [None, "anaxigraph-reads-v1", "anaxigraph-reads-v2"])
def test_older_observers_remain_readable_but_cannot_narrow_checks(checkout, observations):
    import json

    path = checkout / ".anaxigraph-v2/test-records/shop.json"
    record = json.loads(path.read_text())
    assert record["provenance"]["status"] == "verified"
    record["provenance"]["observations"] = observations
    path.write_text(json.dumps(record))
    (checkout / "shop/pkg/orders.py").write_text("def place(items):\n    return {}\n")
    suite = picked(checkout)
    assert suite["status"] == "all"
    assert (
        "Older observation rules; update the recorder, then record the suite again"
        in suite["everything_because"]
    )


def test_bound_v2_report_import_preserves_observations_without_authorizing_narrow_checks(
    checkout, run, tmp_path
):
    import json

    reports = Path(
        shutil.copytree(run.parent, tmp_path / "reports", ignore=lambda *_: ["checkout"])
    )
    path = reports / "reads.json"
    report = json.loads(path.read_text())
    report["format"] = "anaxigraph-reads-v2"
    path.write_text(json.dumps(report))
    saved = record(checkout, run, reads=True, reports=reports)
    assert saved["provenance"]["status"] == "verified"
    assert saved["provenance"]["observations"] == "anaxigraph-reads-v2"
    assert saved["sources_executed"] > 0
    (checkout / "shop/pkg/orders.py").write_text("def place(items):\n    return {}\n")
    suite = picked(checkout)
    assert suite["status"] == "all"
    assert any("Older observation rules" in reason for reason in suite["everything_because"])


def test_a_legacy_recording_is_preserved_but_cannot_authorize_narrow_checks(
    checkout, run, tmp_path
):
    import json

    reports = Path(
        shutil.copytree(run.parent, tmp_path / "reports", ignore=lambda *_: ["checkout"])
    )
    path = reports / "reads.json"
    report = json.loads(path.read_text())
    report.pop("recording")
    path.write_text(json.dumps(report))
    path.with_name(path.name + ".children").write_text('{"untrusted": "child log"}\n')
    assert (
        record(checkout, run, reads=True, reports=reports)["provenance"]["status"] == "unverified"
    )
    (checkout / "shop/pkg/orders.py").write_text("def place(items):\n    return {'total': 0}\n")
    suite = picked(checkout)
    assert suite["status"] == "all"
    assert any("source/run is unverified" in reason for reason in suite["everything_because"])


@pytest.mark.parametrize("parallel", [False, True])
def test_completed_and_interrupted_real_runs_have_distinct_recording_status(
    tmp_path, parallel, capsys
):
    import json

    root = tmp_path / "source"
    (root / "tests").mkdir(parents=True)
    (root / "pkg").mkdir()
    (root / "pkg/__init__.py").write_text("")
    (root / "pkg/engine.py").write_text("def value():\n    return 1\n")
    first = root / "tests/test_first.py"
    first.write_text("from pkg.engine import value\ndef test_first():\n    assert value() == 1\n")
    (root / "tests/test_second.py").write_text(
        "from pkg.engine import value\ndef test_second():\n    assert value() > 0\n"
    )
    assert (
        cli.main(
            [
                "record-tests",
                "--start",
                "--repo",
                str(root),
                "--suite",
                "engine",
                "--scope",
                "pkg",
                "tests",
            ]
        )
        == 0
    )
    manifest = json.loads(capsys.readouterr().out)["manifest"]
    environment = os.environ | {
        "PYTHONPATH": str(Path(recorder.__file__).parent),
        "ANAXIGRAPH_ROOT": str(root),
        "ANAXIGRAPH_RECORDING": manifest,
        "ANAXIGRAPH_READS": str(tmp_path / "reads.json"),
        "COVERAGE_FILE": str(tmp_path / ".coverage"),
    }
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "anaxigraph_reads",
        "--cov=pkg",
        "--cov-context=test",
        "--cov-report=",
        f"--junitxml={tmp_path / 'junit.xml'}",
    ]
    if parallel:
        command.extend(["-n", "2"])
    done = subprocess.run(command, cwd=root, env=environment, text=True, capture_output=True)
    assert done.returncode == 0, done.stdout + done.stderr
    report = json.loads((tmp_path / "reads.json").read_text())
    assert report["complete"] is True and report["finished_tests"] == 2
    assert len(report["workers"]) == (2 if parallel else 0)
    app = Application(root)
    kwargs = {
        "suite": "engine",
        "scope": ["tests", "pkg"],
        "junit": [tmp_path / "junit.xml"],
        "reads": tmp_path / "reads.json",
    }
    assert (
        cli.main(
            [
                "record-tests",
                "--repo",
                str(root),
                "--coverage",
                str(tmp_path / ".coverage"),
                "--junit",
                str(tmp_path / "junit.xml"),
                "--reads",
                str(tmp_path / "reads.json"),
                "--suite",
                "engine",
                "--scope",
                "pkg",
                "tests",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["provenance"]["status"] == "verified"
    [suite] = app.tests_for_change(ran=[tmp_path / "junit.xml"], reads=tmp_path / "reads.json")[
        "suites"
    ]
    assert suite["shadow"]["evidence_status"] == "source-bound"
    assert suite["shadow"]["missing_recorded_test_files"] == []
    saved = root / ".anaxigraph-v2/test-records/engine.json"
    before = saved.read_bytes()
    if parallel:
        (tmp_path / "reads.json.gw0").unlink()
    else:
        first.write_text(first.read_text().replace("== 1", "== 999"))
        Application(root).start_recording("engine")
        done = subprocess.run(
            [*command, "--maxfail=1"], cwd=root, env=environment, text=True, capture_output=True
        )
        assert done.returncode == 1
        report = json.loads((tmp_path / "reads.json").read_text())
        assert report["complete"] is False and report["finished_tests"] == 1
    with pytest.raises(ValueError):
        app.record_tests(tmp_path / ".coverage", **kwargs)
    assert saved.read_bytes() == before


def test_reach_of_a_file_is_what_a_real_edit_then_picks(checkout):
    from anaxigraph.application import selection

    app = Application(checkout)
    paths = ["shop/pkg/settings.py", "shop/pkg/labels.py", "shop/tests/test_tool.py", "nowhere.py"]
    [found] = selection.test_reach(app, app._live(), paths)
    rows = {row["path"]: row for row in found["files"]}
    assert found["suite"] == "shop" and "top_level" in found["advice"]
    assert set(rows) == set(paths) - {"nowhere.py"}
    assert all(
        found["every_change"] <= row["inside_function"] <= row["top_level"] <= 1
        for row in rows.values()
    )
    assert (
        rows["shop/pkg/settings.py"]["top_level"] > rows["shop/pkg/settings.py"]["inside_function"]
    )
    [suite] = app.prepare_change("Change how an order label is written")["test_reach"]
    assert suite["suite"] == "shop" and suite["every_change"] == found["every_change"]

    # The simulated shares are the selection's own: a real edit of each kind picks the same.
    settings, labels = checkout / "shop/pkg/settings.py", checkout / "shop/pkg/labels.py"
    settings.write_text("LIMIT = 4\n")
    assert picked(checkout)["time_share"] == rows["shop/pkg/settings.py"]["top_level"]
    settings.write_text("LIMIT = 3\n")
    labels.write_text(labels.read_text().replace("Order", "Ordre"))
    assert picked(checkout)["time_share"] == rows["shop/pkg/labels.py"]["inside_function"]


def test_reach_is_empty_without_a_record(tmp_path):
    (tmp_path / "one.py").write_text("VALUE = 1\n")
    assert Application(tmp_path).prepare_change("Change the value")["test_reach"] == []
