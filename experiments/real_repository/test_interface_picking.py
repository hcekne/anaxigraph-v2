"""The trial rule: follow the names that changed, and fall back when they cannot be told."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import interface_picking  # noqa: E402

names = interface_picking.changed_names


def test_changed_names_and_when_they_cannot_be_told_apart():
    # A changed name carries to the names whose import-time code refers to it.
    assert names("A = 1\nB = A + 1\nC = 3\n", "A = 2\nB = A + 1\nC = 3\n", False, True) == {
        "A",
        "B",
    }
    # A function body is not import-time code, unless the record saw it run at import.
    body = ("def f():\n    return 1\n", "def f():\n    return 2\n")
    assert names(*body, False, True) == set() and names(*body, True, True) == {"f"}
    assert names("print(1)\n", "print(2)\n", False, True) == "code outside any definition changed"
    assert names("A = 1\nB = 2\n", "A = 1\n", False, True) == "a name was removed"
    assert names("A = 1\nB = 2\n", "A = 1\n", False, False) == {"B"}
    route = "@router.get('/a')\ndef page():\n    return 1\n"
    registering = "a definition that may register itself changed"
    assert names(route, route.replace("/a", "/b"), False, True) == registering
    assert names(route, route.replace("/a", "/b"), False, False) == {"page"}
    plain = "@property\ndef page(self):\n    return 1\n"
    assert names("X = 1\n" + plain, "X = 2\n" + plain, False, True) == {"X"}
    assert names(None, "A = 1\n", False, True) == "new file"


def test_only_the_files_that_take_a_changed_name_are_followed(recorded, capsys):
    root, record = recorded
    options = ["--suite", "shop", "--record", str(record), "--history", "commits"]
    found = interface_picking.main([str(root), *options])
    variants = found["variants"]

    # RATE is used by nothing, LIMIT by one file with two test files.
    assert variants["today"]["mean"] == pytest.approx(1 / 2, abs=0.001)
    assert variants["by name, strict"]["mean"] == pytest.approx(1 / 3, abs=0.001)
    assert variants["every top-level change stays local"]["mean"] == pytest.approx(1 / 6, abs=0.001)
    assert found["rows"]["by name, strict"] == pytest.approx([1 / 3, 0, 2 / 3, 1 / 3], abs=0.001)
    assert found["outcomes"]["by name, strict"] == {"followed by name": 2}
    assert found["left_out"] == 0
    assert "by name, strict" in capsys.readouterr().out
