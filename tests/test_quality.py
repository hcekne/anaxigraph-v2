"""Check real regressions and keep simple nested route handlers out of false debt."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "quality.py"


def quality_module():
    spec = importlib.util.spec_from_file_location("quality", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project(tmp_path: Path, source: str) -> tuple[Path, Path]:
    package = tmp_path / "src" / "anaxigraph"
    package.mkdir(parents=True)
    path = package / "app.py"
    path.write_text(source)
    budget = tmp_path / "quality-budget.json"
    budget.write_text(
        json.dumps(
            {
                "version": 1,
                "limits": {
                    "production_physical_lines": 100,
                    "file_physical_lines": 100,
                    "package_data_bytes": 1000,
                    "new_function_complexity": 2,
                },
                "existing_complexity": {},
            }
        )
    )
    return path, budget


def test_nested_handlers_are_measured_independently(tmp_path):
    path, _ = project(
        tmp_path,
        """def routes():
    def first(value):
        if value:
            return 1
        return 0
    def second(value):
        if value:
            return 2
        return 0
    return first, second
""",
    )
    functions = quality_module().functions(path, tmp_path)
    assert {row["id"].split(":")[1]: row["complexity"] for row in functions} == {
        "routes": 1,
        "routes.first": 2,
        "routes.second": 2,
    }


def test_cli_fails_new_complexity_and_accepts_an_explicit_existing_limit(tmp_path):
    _, budget = project(
        tmp_path,
        "def work(a, b):\n    if a:\n        return 1\n    if b:\n        return 2\n    return 0\n",
    )
    command = [sys.executable, str(SCRIPT), "--root", str(tmp_path)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1
    assert "complexity 3 exceeds 2" in result.stdout
    config = json.loads(budget.read_text())
    config["existing_complexity"]["src/anaxigraph/app.py:work"] = 3
    budget.write_text(json.dumps(config))
    assert subprocess.run(command, capture_output=True).returncode == 0
    (tmp_path / "src/anaxigraph/app.py").write_text(
        "def work(a, b):\n    if a and b:\n        return 1\n    if b:\n        return 2\n    return 0\n"
    )
    assert subprocess.run(command, capture_output=True).returncode == 1


def test_comments_prose_and_frontend_lines_count_against_size(tmp_path):
    path, budget = project(
        tmp_path, '"""Purpose.\nLong explanation.\n"""\n# A comment.\nvalue = 1\n'
    )
    (path.parent / "app.js").write_text("// Frontend comment.\nconst value = 1;\n")
    report = quality_module().measure(tmp_path)
    assert report["production"]["physical_lines"] == 7
    config = json.loads(budget.read_text())
    config["limits"]["production_physical_lines"] = 6
    assert "production.physical_lines: 7 exceeds 6" in quality_module().check(report, config)


def test_reviewed_changes_require_a_concrete_reason(tmp_path):
    _, budget = project(
        tmp_path, "def work(a, b):\n    if a and b:\n        return 1\n    return 0\n"
    )
    module = quality_module()
    report, config = module.measure(tmp_path), json.loads(budget.read_text())
    config["reviewed_changes"] = {"src/anaxigraph/app.py:work": {"ceiling": 3, "reason": ""}}
    with pytest.raises(ValueError, match="concrete explanation"):
        module.check(report, config)
    config["reviewed_changes"]["src/anaxigraph/app.py:work"]["reason"] = (
        "A new explicit compatibility condition preserves the existing public operation."
    )
    assert module.check(report, config) == []


def test_package_data_bytes_are_visible_and_bounded(tmp_path):
    path, budget = project(tmp_path, "value = 1\n")
    (path.parent / "catalogue.json").write_text(json.dumps({"text": "x" * 1100}))
    module = quality_module()
    report = module.measure(tmp_path)
    config = json.loads(budget.read_text())
    assert report["package_data"]["bytes"] > 1100
    assert any("package_data.bytes" in error for error in module.check(report, config))
