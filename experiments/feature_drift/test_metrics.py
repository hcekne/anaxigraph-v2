import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics  # noqa: E402

SOURCE = """
def price(amount, vip):
    if vip and amount > 100:
        return amount * 0.9
    return amount
"""


def write(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)


def test_paths_and_static_shape(tmp_path):
    write(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/a.py": "from app import b\n\ndef f():\n    return b.g()\n",
            "app/b.py": "from . import a\n\ndef g():\n    return 1\n",
            "app/c.py": "import app.b\n",
            "tests/test_a.py": "def test_x():\n    assert True\n",
            ".venv/lib/x.py": "ignored = 1\n",
        },
    )
    assert metrics.production_files(tmp_path) == [
        "app/__init__.py",
        "app/a.py",
        "app/b.py",
        "app/c.py",
    ]
    shape = metrics.static_metrics(tmp_path)
    assert shape["import_cycles"] == 1 and shape["modules_in_cycles"] == 2
    assert shape["test_files"] == 1 and shape["max_fan_in"] == 2


def test_duplication_counts_repeated_blocks():
    block = "\n".join(f"value_{i} = compute({i})" for i in range(6))
    assert metrics.duplication({"a.py": block, "b.py": block}) == 1.0
    assert metrics.duplication({"a.py": block, "b.py": "x = 1\n"}) == 0.0


def test_mutations_change_behaviour_inside_functions():
    sites = metrics.candidate_mutants(SOURCE)
    assert sites and all(line >= 3 for _, line in sites)
    kinds = set()
    for index, _ in sites:
        mutated, (kind, _) = metrics.mutate(SOURCE, index)
        kinds.add(kind)
        namespace: dict = {}
        exec(mutated, namespace)
        original: dict = {}
        exec(SOURCE, original)
        cases = [(200, True), (50, True), (200, False), (101, True)]
        assert any(namespace["price"](*c) != original["price"](*c) for c in cases), kind
    assert {"compare", "boolop", "return_none", "negate_if", "constant"} <= kinds


def test_numbits_decoding():
    assert metrics.numbits_to_lines(bytes([0b00000110, 0b1])) == {1, 2, 8}


def test_diff_stats_split_production_and_tests(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    write(tmp_path, {"app/a.py": "x = 1\n", "tests/test_a.py": "y = 1\n"})
    git("add", "-A")
    git("commit", "-qm", "one")
    base = metrics.git(tmp_path, "rev-parse", "HEAD").strip()
    write(
        tmp_path,
        {
            "app/a.py": "x = 2\nz = 3\n",
            "app/new.py": "n = 1\n",
            "tests/test_a.py": "y = 2\n",
            "README.md": "hi\n",
        },
    )
    git("add", "-A")
    git("commit", "-qm", "two")
    stats = metrics.diff_stats(tmp_path, base)
    assert stats["prod_added_files"] == 1 and stats["prod_modified_files"] == 1
    assert stats["test_files_touched"] == 1 and stats["other_files_touched"] == 1
    assert stats["prod_lines_added"] == 3 and stats["prod_lines_deleted"] == 1
