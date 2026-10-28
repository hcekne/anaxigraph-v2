import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scoring  # noqa: E402

FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/store.py": "def save(record):\n    return record\n",
    "src/pkg/service.py": "from pkg.store import save\n\ndef handle(x):\n    return save(x)\n",
    "src/pkg/api.py": "from pkg import service\n\ndef route():\n    return service.handle(1)\n",
    "src/pkg/cli.py": "from .api import route\n",
    "src/pkg/unrelated.py": "VALUE = 'restore backups'\n",
    "tests/test_store.py": "from pkg.store import save\n",
    "tests/test_misc.py": "import os\n",
}


def test_test_detection():
    assert scoring.is_test("tests/test_store.py")
    assert scoring.is_test("src/pkg/test_helpers.py")
    assert scoring.is_test("conftest.py")
    assert not scoring.is_test("src/pkg/testing_utils.py")


def test_rank_and_recall():
    assert scoring.rank_of_first_hit(["a", "b", "c"], {"c"}) == 3
    assert scoring.rank_of_first_hit(["a"], {"z"}) is None
    assert scoring.recall({"a", "b"}, {"a", "c"}) == 0.5
    assert scoring.recall({"a"}, set()) is None


def test_module_name_handles_src_and_packages():
    assert scoring.module_name("src/pkg/store.py") == "pkg.store"
    assert scoring.module_name("src/pkg/__init__.py") == "pkg"
    assert scoring.module_name("benchmarks/run.py") == "benchmarks.run"


def test_grep_importers_follows_two_levels_of_absolute_and_relative_imports():
    one = scoring.grep_importers(FILES, {"src/pkg/store.py"}, depth=1)
    assert one == {"src/pkg/store.py", "src/pkg/service.py", "tests/test_store.py"}
    two = scoring.grep_importers(FILES, {"src/pkg/store.py"})
    assert "src/pkg/api.py" in two and "src/pkg/cli.py" not in two
    three = scoring.grep_importers(FILES, {"src/pkg/store.py"}, depth=3)
    assert "src/pkg/cli.py" in three


def test_grep_tests_and_owners():
    assert scoring.grep_tests(FILES, {"src/pkg/store.py"}) == {"tests/test_store.py"}
    production = {p: t for p, t in FILES.items() if not scoring.is_test(p)}
    assert scoring.grep_owners(production, "Restore backups safely", 1) == ["src/pkg/unrelated.py"]


def test_summary_excludes_unknown_metrics_from_denominators():
    rows = [
        {"grep_owner_rank": 1, "oracle_test_recall_grep": 1.0},
        {"grep_owner_rank": None, "oracle_test_recall_grep": None},
        {"grep_owner_rank": 3},
        {"grep_owner_rank": None, "owner_evaluable": False},
    ]
    summary = scoring.summarize(rows)
    assert summary["owners"]["grep"] == {"hit@1": "1/3", "hit@3": "2/3"}
    assert summary["owner_evaluable_commits"] == 3
    assert summary["metrics"]["oracle_test_recall_grep"] == {"mean": 1.0, "n": 1}


def test_cochanged_ranks_logical_coupling_and_ignores_huge_commits():
    history = [
        {"a.py", "b.py"},
        {"a.py", "b.py", "c.py"},
        {"a.py", "c.py"},
        {"a.py", "b.py"},
        {"b.py", "d.py"},
        {"a.py"} | {f"bulk{i}.py" for i in range(40)},
    ]
    assert scoring.cochanged(history, "a.py", 1) == {"b.py"}
    assert scoring.cochanged(history, "a.py", 5) == {"b.py", "c.py"}
    assert scoring.cochanged(history, "z.py", 5) == set()
