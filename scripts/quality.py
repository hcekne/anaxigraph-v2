"""Measure package size and prevent new function-complexity debt in CI.

Physical lines include comments, docstrings, prompts, HTML, CSS and JavaScript.
The Python decision score is one plus branch/boolean/comprehension decisions in
one function's scope; nested callables are measured independently. Registering
twenty small HTTP handlers therefore does not make the registration function
look like a twenty-branch algorithm.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

CODE_SUFFIXES = {".py", ".js", ".css", ".html"}


class Decisions(ast.NodeVisitor):
    def __init__(self) -> None:
        self.score = 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        pass

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(
            node, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)
        ):
            self.score += 1
        elif isinstance(node, ast.BoolOp):
            self.score += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            self.score += 1 + len(node.ifs)
        elif isinstance(node, ast.Match):
            self.score += sum(
                not (isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None)
                for case in node.cases
            ) + sum(case.guard is not None for case in node.cases)
        super().generic_visit(node)


def functions(path: Path, root: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                counter = Decisions()
                for statement in child.body:
                    counter.visit(statement)
                found.append(
                    {
                        "id": f"{path.relative_to(root).as_posix()}:{name}",
                        "line": child.lineno,
                        "complexity": counter.score,
                    }
                )
                visit(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".")
            else:
                visit(child, prefix)

    visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return found


def counts(paths: list[Path]) -> dict[str, int]:
    text = [path.read_text(encoding="utf-8") for path in paths]
    return {
        "files": len(paths),
        "physical_lines": sum(len(body.splitlines()) for body in text),
        "nonblank_lines": sum(bool(line.strip()) for body in text for line in body.splitlines()),
        "bytes": sum(path.stat().st_size for path in paths),
    }


def measure(root: Path) -> dict[str, Any]:
    package = root / "src" / "anaxigraph"
    production = sorted(
        path for path in package.rglob("*") if path.is_file() and path.suffix in CODE_SUFFIXES
    )
    data = sorted(path for path in package.rglob("*.json") if path.is_file())
    report: dict[str, Any] = {
        "production": counts(production),
        "package_data": counts(data),
        "file_lines": {},
        "functions": [],
    }
    for path in production:
        report["file_lines"][path.relative_to(root).as_posix()] = len(path.read_text().splitlines())
        if path.suffix == ".py":
            report["functions"].extend(functions(path, root))
    for name in ("tests", "experiments", "scripts"):
        paths = sorted(
            path
            for path in (root / name).rglob("*")
            if path.is_file() and path.suffix in CODE_SUFFIXES
        )
        report[name] = counts(paths)
    return report


def check(report: dict[str, Any], config: dict[str, Any]) -> list[str]:
    limits, messages = config["limits"], []
    for section, metric, setting in (
        ("production", "physical_lines", "production_physical_lines"),
        ("package_data", "bytes", "package_data_bytes"),
    ):
        actual, limit = report[section][metric], limits[setting]
        if actual > limit:
            messages.append(f"{section}.{metric}: {actual} exceeds {limit}")
    for path, actual in report["file_lines"].items():
        if actual > limits["file_physical_lines"]:
            messages.append(
                f"{path}: {actual} physical lines exceeds {limits['file_physical_lines']}"
            )
    for function in report["functions"]:
        ceiling = config["existing_complexity"].get(
            function["id"], limits["new_function_complexity"]
        )
        approved = config.get("reviewed_changes", {}).get(function["id"])
        if approved is not None:
            if not isinstance(approved.get("reason"), str) or len(approved["reason"].strip()) < 20:
                raise ValueError(f"{function['id']}: reviewed limit needs a concrete explanation")
            ceiling = approved["ceiling"]
        if function["complexity"] > ceiling:
            messages.append(
                f"{function['id']}:{function['line']}: complexity {function['complexity']} exceeds {ceiling}"
            )
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    config = json.loads((args.config or root / "quality-budget.json").read_text())
    if config.get("version") != 1:
        raise ValueError("Unsupported quality budget version")
    report = measure(root)
    report["violations"] = check(report, config)
    report["python_functions"] = len(report["functions"])
    report["functions"] = [
        function
        for function in report["functions"]
        if function["complexity"] > config["limits"]["new_function_complexity"]
    ]
    report["complexity_metric"] = "scope-local decisions including boolean/comprehension branches"
    print(json.dumps(report, indent=2))
    if report["violations"]:
        print(
            "\nReview these regressions; simplify the change or deliberately revise the documented budget."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
