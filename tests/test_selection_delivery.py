"""Every observed consumer remains retrievable when a compact reply reaches its limit."""

from __future__ import annotations

import asyncio
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
from anaxigraph.interfaces.mcp import create_mcp

CONSUMERS = [f"tests/test_{index:03}.py" for index in range(201)]


@pytest.fixture(scope="module")
def many_recorded_consumers(tmp_path_factory):
    folder = tmp_path_factory.mktemp("many-consumers")
    root = folder / "source"
    files = {
        "app/__init__.py": "",
        "app/shared.py": "def value():\n    return 1\n",
        "app/other.py": "def other():\n    return 2\n",
        "tests/test_independent.py": (
            "from app.other import other\ndef test_other():\n    assert other() == 2\n"
        ),
    }
    files.update(
        dict.fromkeys(
            CONSUMERS, "from app.shared import value\ndef test_value():\n    assert value() == 1\n"
        )
    )
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    app = Application(root)
    manifest = app.start_recording("consumers")["manifest"]
    environment = os.environ | {
        "PYTHONPATH": str(Path(recorder.__file__).parent),
        "PYTHONDONTWRITEBYTECODE": "1",
        "ANAXIGRAPH_ROOT": str(root),
        "ANAXIGRAPH_READS": str(folder / "reads.json"),
        "ANAXIGRAPH_RECORDING": manifest,
        "COVERAGE_FILE": str(folder / ".coverage"),
    }
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "anaxigraph_reads",
            "--cov=app",
            "--cov-context=test",
            "--cov-report=",
            f"--junitxml={folder / 'baseline.xml'}",
        ],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    saved = app.record_tests(
        folder / ".coverage",
        suite="consumers",
        scope=["app", "tests"],
        junit=[folder / "baseline.xml"],
        reads=folder / "reads.json",
    )
    assert saved["test_files"] == 202
    assert saved["provenance"]["status"] == "verified"
    assert saved["provenance"]["observations"] == "anaxigraph-reads-v3"
    return root


@pytest.fixture
def changed_consumers(many_recorded_consumers, tmp_path):
    root = Path(shutil.copytree(many_recorded_consumers, tmp_path / "checkout"))
    app = Application(root)
    prepared = app.prepare_change("Change the shared operation behind its existing contract")
    (root / "app/shared.py").write_text("def value():\n    return 3\n")
    return app, prepared["baseline_id"]


def _reply(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], dict):
        return value[1]
    return json.loads(next(block.text for block in value if block.type == "text"))


def _assert_compact(suite):
    assert suite["status"] == "selected"
    assert suite["selected"] == 201 and suite["total"] == 202
    assert suite["test_files"] == CONSUMERS[:200]
    assert suite["test_files_truncated"] is True


def _assert_full(suite):
    assert suite["status"] == "selected"
    assert suite["selected"] == 201 and suite["total"] == 202
    assert suite["test_files"] == CONSUMERS
    assert not suite.get("test_files_truncated")
    assert "tests/test_independent.py" not in suite["test_files"]


def test_mcp_expands_every_observed_consumer_without_changing_compact_defaults(changed_consumers):
    app, _ = changed_consumers
    server = create_mcp(app)

    async def exercise():
        _assert_compact(_reply(await server.call_tool("tests_for_change", {}))["suites"][0])
        _assert_full(
            _reply(await server.call_tool("tests_for_change", {"full": True}))["suites"][0]
        )
        _assert_compact(_reply(await server.call_tool("tests_for_change", {}))["suites"][0])
        tool = next(tool for tool in await server.list_tools() if tool.name == "tests_for_change")
        assert tool.inputSchema["properties"]["full"]["type"] == "boolean"
        assert tool.inputSchema["properties"]["full"]["default"] is False

    asyncio.run(exercise())


def test_full_validation_expands_test_consumers_without_moving_its_baseline(changed_consumers):
    app, baseline = changed_consumers
    compact = app.validate_change()
    expanded = app.validate_change(full=True)
    _assert_compact(compact["tests"]["suites"][0])
    _assert_full(expanded["tests"]["suites"][0])
    assert compact["baseline_id"] == expanded["baseline_id"] == baseline
    assert compact["candidate_snapshot_id"] == expanded["candidate_snapshot_id"]
    assert expanded["tests"]["snapshot_id"] == expanded["candidate_snapshot_id"]
    assert expanded["checkout_current"] is True


def test_cli_paths_already_returns_every_observed_consumer(changed_consumers, capsys):
    app, _ = changed_consumers
    assert cli.main(["tests", "--repo", str(app.root), "--paths"]) == 0
    assert capsys.readouterr().out.splitlines() == CONSUMERS


def test_cli_full_validation_expands_consumers_without_qualifying_unchecked_work(
    changed_consumers, capsys
):
    app, baseline = changed_consumers
    assert cli.main(["validate", "--repo", str(app.root), "--full"]) == 0
    validation = json.loads(capsys.readouterr().out)
    _assert_full(validation["tests"]["suites"][0])
    assert validation["baseline_id"] == baseline
    assert validation["validation_status"] == "unchecked"
    assert validation["completion_allowed"] is False
