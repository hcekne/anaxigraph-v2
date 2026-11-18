"""Keep the dashboard's real orchestration in the existing test run without browser dependencies."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Frontend checks need Node.js")
def test_dashboard_loading_lifecycle():
    test = Path(__file__).with_name("dashboard_loading.test.cjs")
    result = subprocess.run(["node", "--test", str(test)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
