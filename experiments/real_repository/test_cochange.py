"""Historical coupling stays scoped, retrospective and separate from dependency facts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cochange import capture_history, cochange_advice  # noqa: E402


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def commit(root, changes):
    for path, source in changes.items():
        (root / path).write_text(source)
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    return git(root, "rev-parse", "HEAD")


def history_repo(root):
    git(root, "init", "-q")
    commit(root, {"alpha.py": "def alpha():\n    return 1\n"})
    commit(root, {"beta.py": "def beta():\n    return 1\n"})
    commit(root, {"gamma.py": "def gamma():\n    return 1\n"})
    return commit(
        root, {"alpha.py": "def alpha():\n    return 2\n", "beta.py": "def beta():\n    return 2\n"}
    )


def test_capture_uses_only_requested_revision_and_current_eligible_paths(tmp_path):
    baseline = history_repo(tmp_path)
    commit(
        tmp_path,
        {"alpha.py": "def alpha():\n    return 3\n", "gamma.py": "def gamma():\n    return 3\n"},
    )
    observed = capture_history(tmp_path, baseline, {"alpha.py", "beta.py", "gamma.py"})
    advice = cochange_advice(observed, {"alpha.py"})
    assert advice["through_revision"] == baseline
    assert [item["path"] for item in advice["suggestions"]] == ["beta.py"]
    assert advice["suggestions"][0]["cochange_commits"] == 1
    restricted = capture_history(tmp_path, baseline, {"alpha.py", "gamma.py"})
    assert cochange_advice(restricted, {"alpha.py"})["suggestions"] == []


def test_associations_are_bounded_and_bulk_changes_do_not_manufacture_coupling():
    history = {
        "status": "available",
        "changes": [
            {"size": 2, "paths": ["a.py", "b.py"]},
            {"size": 2, "paths": ["a.py", "b.py"]},
            {"size": 2, "paths": ["a.py", "c.py"]},
            {"size": 90, "paths": ["a.py", "unrelated.py"]},
        ],
    }
    result = cochange_advice(history, {"a.py"}, limit=1)
    assert result["suggestions"] == [
        {"path": "b.py", "cochange_commits": 2, "with_paths": ["a.py"], "support_fraction": 0.667}
    ]
    assert result["suggestions_total"] == 2
    assert result["supporting_commits"] == 3
    assert "dependencies" in result["interpretation"]


def test_no_history_is_a_visible_normal_condition(tmp_path):
    result = cochange_advice(capture_history(tmp_path, None, {"a.py"}), {"a.py"})
    assert result["status"] == "unavailable"
    assert result["suggestions"] == []
