"""The CI selector contract. Safety and cost are measured separately by planted bugs."""

import json
import os
import subprocess
import time

from conftest import CANDIDATE


def select(*paths: str) -> list[str]:
    command = json.loads((CANDIDATE / "ci.json").read_text())["select"]
    env = {**os.environ, "PATH": f"{CANDIDATE / '.venv' / 'bin'}:{os.environ['PATH']}"}
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=CANDIDATE,
        input="".join(p + "\n" for p in paths),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert time.monotonic() - started < 10
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def production_files() -> list[str]:
    return sorted(
        str(p.relative_to(CANDIDATE))
        for p in CANDIDATE.rglob("*.py")
        if ".venv" not in p.parts
        and "tests" not in p.parts
        and not p.name.startswith("test_")
        and p.name != "conftest.py"
        and not any(part.startswith(".") for part in p.relative_to(CANDIDATE).parts)
    )


def test_contract_file():
    data = json.loads((CANDIDATE / "ci.json").read_text())
    assert isinstance(data["select"], list) and data["select"]


def test_docs_only_changes_select_nothing():
    assert select("README.md") == []
    assert select("docs/requests/01-weather.md", "docs/notes.md") == []


def test_build_inputs_select_everything():
    for path in ("pyproject.toml", "uv.lock", "ci.json"):
        assert select(path) == ["ALL"], path


def test_every_production_file_selects_existing_targets():
    files = production_files()
    assert files
    for path in files:
        targets = select(path)
        assert targets, f"{path} selects no tests"
        if targets != ["ALL"]:
            for target in targets:
                assert (CANDIDATE / target.split("::")[0]).exists(), (path, target)


def test_changed_test_file_selects_itself():
    tests = sorted(str(p.relative_to(CANDIDATE)) for p in (CANDIDATE / "tests").rglob("test_*.py"))
    assert tests
    targets = select(tests[0])
    assert targets == ["ALL"] or any(
        tests[0] == t.split("::")[0] or tests[0].startswith(t.rstrip("/") + "/") for t in targets
    )
