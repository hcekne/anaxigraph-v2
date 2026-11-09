"""Neutral measurements of a candidate checkout. Nothing here imports AnaxiGraph.

- Static shape: size, function length/complexity, import cycles, coupling and duplication.
- Change amplification: what one step's commit touched.
- CI quality: per production file, how many tests the candidate's selector picks versus the
  tests that actually execute that file, and whether planted single-file bugs that the suite
  catches are also caught by the selected tests.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import random
import shlex
import sqlite3
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path, PurePosixPath

SKIP_PARTS = {".venv", "node_modules", "__pycache__", "anaxigraph"}


def is_scratch(path: str) -> bool:
    """Tool output that agents sometimes leave in the checkout, such as pytest base temps."""
    parts = PurePosixPath(path).parts[:-1]
    return any(p.startswith(".") or p in SKIP_PARTS or p.startswith("pytest-of-") for p in parts)


def is_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = parts[-1]
    return "tests" in parts or "test" in parts or name.startswith("test_") or name == "conftest.py"


def python_files(root: Path) -> list[str]:
    found = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if is_scratch(rel.as_posix()):
            continue
        found.append(rel.as_posix())
    return sorted(found)


def production_files(root: Path) -> list[str]:
    return [p for p in python_files(root) if not is_test_path(p)]


def code_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))


class _Decisions(ast.NodeVisitor):
    def __init__(self) -> None:
        self.score = 1

    def visit_FunctionDef(self, node):  # nested functions are measured on their own
        pass

    visit_AsyncFunctionDef = visit_ClassDef = visit_Lambda = visit_FunctionDef

    def generic_visit(self, node):
        if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.While, ast.ExceptHandler, ast.With)):
            self.score += 1
        elif isinstance(node, ast.BoolOp):
            self.score += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            self.score += 1 + len(node.ifs)
        elif isinstance(node, ast.match_case):
            self.score += 1
        super().generic_visit(node)


def functions(tree: ast.AST) -> list[dict]:
    rows = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            counter = _Decisions()
            for statement in node.body:
                counter.visit(statement)
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            rows.append({"name": node.name, "lines": length, "complexity": counter.score})
    return rows


def module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imports(path: str, tree: ast.AST, known: set[str]) -> set[str]:
    """Internal modules imported by one file (absolute and relative, including `from x import y`)."""
    package = module_name(path).split(".")
    if not path.endswith("__init__.py"):
        package = package[:-1]
    found = set()
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                base = ".".join([*anchor, base] if base else anchor)
            names = [base] + [f"{base}.{alias.name}" for alias in node.names]
        for name in names:
            while name and name not in known:
                name = name.rpartition(".")[0]
            if name:
                found.add(name)
    found.discard(module_name(path))
    return found


def strongly_connected(graph: dict[str, set[str]]) -> list[set[str]]:
    index, low, stack, on_stack, result = {}, {}, [], set(), []
    counter = [0]

    def visit(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph.get(node, ()):
            if target not in index:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], index[target])
        if low[node] == index[node]:
            component = set()
            while True:
                item = stack.pop()
                on_stack.discard(item)
                component.add(item)
                if item == node:
                    break
            result.append(component)

    for node in sorted(graph):
        if node not in index:
            visit(node)
    return result


def duplication(texts: dict[str, str], window: int = 6) -> float:
    """Share of non-trivial production lines inside a 6-line block that appears twice or more."""
    blocks: dict[str, list[tuple[str, int]]] = defaultdict(list)
    lines_by_file = {}
    for path, text in texts.items():
        lines = [
            line.strip()
            for line in text.splitlines()
            if len(line.strip()) > 3 and not line.strip().startswith(("#", "import ", "from "))
        ]
        lines_by_file[path] = lines
        for start in range(len(lines) - window + 1):
            digest = hashlib.sha1("\n".join(lines[start : start + window]).encode()).hexdigest()
            blocks[digest].append((path, start))
    duplicated: set[tuple[str, int]] = set()
    for places in blocks.values():
        if len(places) > 1:
            for path, start in places:
                duplicated.update((path, start + offset) for offset in range(window))
    total = sum(len(lines) for lines in lines_by_file.values())
    return round(len(duplicated) / total, 4) if total else 0.0


def static_metrics(root: Path) -> dict:
    production = production_files(root)
    tests = [p for p in python_files(root) if is_test_path(p)]
    texts = {p: (root / p).read_text(encoding="utf-8", errors="replace") for p in production}
    trees = {}
    for path, text in texts.items():
        try:
            trees[path] = ast.parse(text)
        except SyntaxError:
            pass
    known = {module_name(p) for p in trees}
    graph = {module_name(p): imports(p, tree, known) for p, tree in trees.items()}
    cycles = [c for c in strongly_connected(graph) if len(c) > 1]
    fan_in: dict[str, int] = defaultdict(int)
    for targets in graph.values():
        for target in targets:
            fan_in[target] += 1
    funcs = [f for tree in trees.values() for f in functions(tree)]
    sizes = {p: code_lines(t) for p, t in texts.items()}
    return {
        "prod_files": len(production),
        "prod_loc": sum(sizes.values()),
        "test_files": len(tests),
        "test_loc": sum(
            code_lines((root / p).read_text(encoding="utf-8", errors="replace")) for p in tests
        ),
        "largest_module_loc": max(sizes.values(), default=0),
        "functions": len(funcs),
        "max_function_lines": max((f["lines"] for f in funcs), default=0),
        "mean_function_lines": round(sum(f["lines"] for f in funcs) / len(funcs), 2)
        if funcs
        else 0,
        "max_complexity": max((f["complexity"] for f in funcs), default=0),
        "functions_over_complexity_10": sum(f["complexity"] > 10 for f in funcs),
        "import_cycles": len(cycles),
        "modules_in_cycles": sum(len(c) for c in cycles),
        "mean_fan_out": round(sum(map(len, graph.values())) / len(graph), 2) if graph else 0,
        "max_fan_in": max(fan_in.values(), default=0),
        "duplication": duplication(texts),
    }


def git(repo: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    ).stdout


def diff_stats(repo: Path, base: str, head: str = "HEAD") -> dict:
    """What one commit range touched, split into production, tests and other files."""
    status = {}
    for line in git(repo, "diff", "--name-status", "--no-renames", base, head).splitlines():
        parts = line.split("\t")
        if not is_scratch(parts[-1]):
            status[parts[-1]] = parts[0][0]
    stats = {
        "prod_added_files": 0,
        "prod_modified_files": 0,
        "prod_deleted_files": 0,
        "test_files_touched": 0,
        "other_files_touched": 0,
        "prod_lines_added": 0,
        "prod_lines_deleted": 0,
        "test_lines_added": 0,
        "test_lines_deleted": 0,
        "changed_files": sorted(status),
    }
    for line in git(repo, "diff", "--numstat", "--no-renames", base, head).splitlines():
        added, deleted, path = line.split("\t", 2)
        if is_scratch(path):
            continue
        added = int(added) if added.isdigit() else 0
        deleted = int(deleted) if deleted.isdigit() else 0
        if not path.endswith(".py"):
            stats["other_files_touched"] += 1
        elif is_test_path(path):
            stats["test_files_touched"] += 1
            stats["test_lines_added"] += added
            stats["test_lines_deleted"] += deleted
        else:
            kind = status.get(path, "M")
            key = {"A": "prod_added_files", "D": "prod_deleted_files"}.get(
                kind, "prod_modified_files"
            )
            stats[key] += 1
            stats["prod_lines_added"] += added
            stats["prod_lines_deleted"] += deleted
    return stats


# -- CI selection --------------------------------------------------------------------------


def run(argv: list[str], cwd: Path, timeout: float, env: dict | None = None, stdin: str = ""):
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **(env or {})},
        )
    except subprocess.TimeoutExpired as error:
        return subprocess.CompletedProcess(argv, 124, error.stdout or "", "timeout")


def candidate_env(repo: Path) -> dict:
    return {
        "PATH": f"{repo / '.venv' / 'bin'}:{os.environ.get('PATH', '')}",
        "PYTHONDONTWRITEBYTECODE": "1",
        "VIRTUAL_ENV": str(repo / ".venv"),
        "PYTHONPATH": os.pathsep.join(
            path for path in (str(repo), str(repo / "src"), os.environ.get("PYTHONPATH")) if path
        ),
    }


def kept_addopts(repo: Path) -> list[str]:
    """Options from the candidate's own pytest config that change how tests import.

    The harness blanks `addopts` so coverage, `-x` or plugins cannot interfere, but a suite
    that relies on `--import-mode=importlib` (duplicate test basenames) must keep it.
    """
    import configparser
    import shlex
    import tomllib

    values: list[str] = []
    try:
        opts = tomllib.loads((repo / "pyproject.toml").read_text())["tool"]["pytest"]
        opts = opts["ini_options"]["addopts"]
        values += opts if isinstance(opts, list) else shlex.split(opts)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    for name, section in (
        ("pytest.ini", "pytest"),
        ("tox.ini", "pytest"),
        ("setup.cfg", "tool:pytest"),
    ):
        parser = configparser.ConfigParser()
        try:
            parser.read(repo / name)
            values += shlex.split(parser.get(section, "addopts"))
        except (configparser.Error, ValueError):
            pass
    return [v for v in values if v.startswith("--import-mode=")][-1:]


def pytest_argv(repo: Path) -> list[str]:
    python = str(repo / ".venv" / "bin" / "python")
    return [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *kept_addopts(repo)]


def collect(repo: Path, targets: list[str]) -> list[str] | None:
    """Node ids pytest would run for these targets; None if collection itself fails."""
    if not targets:
        return []
    args = [] if targets == ["ALL"] else targets
    result = run(
        [*pytest_argv(repo), "--collect-only", "-o", "addopts=", *args],
        repo,
        300,
        candidate_env(repo),
    )
    ids = [line.strip() for line in result.stdout.splitlines() if "::" in line]
    if result.returncode not in (0, 5):
        return None
    return ids


def select(repo: Path, changed: list[str]) -> tuple[list[str] | None, float]:
    """Ask the candidate's own selector; None when it is missing or fails."""
    try:
        command = json.loads((repo / "ci.json").read_text())["select"]
    except (OSError, ValueError, KeyError, TypeError):
        return None, 0.0
    started = time.monotonic()
    result = run(command, repo, 60, candidate_env(repo), "".join(p + "\n" for p in changed))
    elapsed = time.monotonic() - started
    if result.returncode != 0:
        return None, elapsed
    return [line.strip() for line in result.stdout.splitlines() if line.strip()], elapsed


def numbits_to_lines(blob: bytes) -> set[int]:
    return {
        index * 8 + bit for index, byte in enumerate(blob) for bit in range(8) if byte >> bit & 1
    }


def junit_file(repo: Path, classname: str) -> str | None:
    """The test module of a JUnit classname such as `tests.api.test_x.TestCase`."""
    parts = classname.split(".")
    for end in range(len(parts), 0, -1):
        path = "/".join(parts[:end]) + ".py"
        if (repo / path).is_file():
            return path
    return None


def coverage_by_test(repo: Path) -> tuple[dict[str, dict[int, set[str]]], dict]:
    """Run the candidate suite once with per-test contexts: file → line → covering test ids."""
    with tempfile.TemporaryDirectory(prefix="drift-cov-") as tmp:
        data = Path(tmp) / ".coverage"
        junit = Path(tmp) / "suite.xml"
        started = time.monotonic()
        result = run(
            [
                *pytest_argv(repo),
                f"--cov={repo}",
                "--cov-context=test",
                "--cov-report=",
                f"--junitxml={junit}",
                "-o",
                "addopts=",
            ],
            repo,
            1800,
            {**candidate_env(repo), "COVERAGE_FILE": str(data)},
        )
        info = {
            "returncode": result.returncode,
            "seconds": round(time.monotonic() - started, 1),
            "command": shlex.join(result.args),
        }
        if junit.exists():
            cases = list(ET.parse(junit).getroot().iter("testcase"))
            failing = [
                case
                for case in cases
                if case.find("failure") is not None or case.find("error") is not None
            ]
            info.update(
                tests=len(cases),
                passed=sum(
                    case.find("failure") is None
                    and case.find("error") is None
                    and case.find("skipped") is None
                    for case in cases
                ),
                failed_tests=[f"{c.get('classname')}::{c.get('name')}" for c in failing][:20],
                failed_files=sorted(
                    {path for c in failing if (path := junit_file(repo, c.get("classname") or ""))}
                ),
                failure_details=[
                    {
                        "test": f"{case.get('classname')}::{case.get('name')}",
                        "message": failure.get("message", "")[:500],
                        "traceback": (failure.text or "")[-2000:],
                    }
                    for case in failing[:8]
                    for failure in [*case.findall("failure"), *case.findall("error")]
                ],
            )
        if not data.exists():
            info["error"] = (result.stdout + result.stderr)[-1500:]
            return {}, info
        db = sqlite3.connect(data)
        files = dict(db.execute("SELECT id, path FROM file"))
        contexts = dict(db.execute("SELECT id, context FROM context"))
        covered: dict[str, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
        for file_id, context_id, blob in db.execute(
            "SELECT file_id, context_id, numbits FROM line_bits"
        ):
            test = contexts[context_id].rsplit("|", 1)[0]
            if not test:
                continue
            path = Path(files[file_id])
            try:
                rel = path.resolve().relative_to(repo.resolve()).as_posix()
            except ValueError:
                continue
            for line in numbits_to_lines(blob):
                covered[rel][line].add(test)
        db.close()
        return {k: dict(v) for k, v in covered.items()}, info


class _Mutator(ast.NodeTransformer):
    """Applies the target-th mutation opportunity; with target None it only records sites."""

    SWAPS = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.GtE: ast.Lt,
             ast.Gt: ast.LtE, ast.LtE: ast.Gt, ast.In: ast.NotIn, ast.NotIn: ast.In,
             ast.Is: ast.IsNot, ast.IsNot: ast.Is}  # fmt: skip

    def __init__(self, target: int | None) -> None:
        self.target, self.sites, self.applied = target, [], None

    def _hit(self, node: ast.AST, kind: str) -> bool:
        self.sites.append(node.lineno)
        if len(self.sites) - 1 == self.target:
            self.applied = (kind, node.lineno)
            return True
        return False

    def visit_Compare(self, node):
        self.generic_visit(node)
        if type(node.ops[0]) in self.SWAPS and self._hit(node, "compare"):
            node.ops[0] = self.SWAPS[type(node.ops[0])]()
        return node

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        if self._hit(node, "boolop"):
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, bool):
            if self._hit(node, "constant"):
                return ast.copy_location(ast.Constant(not node.value), node)
        elif isinstance(node.value, int) and self._hit(node, "constant"):
            return ast.copy_location(ast.Constant(node.value + 1), node)
        return node

    def visit_Return(self, node):
        self.generic_visit(node)
        trivial = isinstance(node.value, ast.Constant) and node.value.value is None
        if node.value is not None and not trivial and self._hit(node, "return_none"):
            node.value = ast.Constant(None)
        return node

    def visit_If(self, node):
        self.generic_visit(node)
        if self._hit(node, "negate_if"):
            node.test = ast.UnaryOp(ast.Not(), node.test)
        return node


def _inside_functions(tree: ast.AST) -> set[int]:
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in node.body:
                lines.update(range(child.lineno, (child.end_lineno or child.lineno) + 1))
    return lines


def mutate(source: str, index: int) -> tuple[str, tuple[str, int]] | None:
    mutator = _Mutator(index)
    mutated = mutator.visit(ast.parse(source))
    if mutator.applied is None:
        return None
    ast.fix_missing_locations(mutated)
    return ast.unparse(mutated), mutator.applied


def candidate_mutants(source: str) -> list[tuple[int, int]]:
    """(opportunity index, line) for every opportunity inside a function body."""
    tree = ast.parse(source)
    body_lines = _inside_functions(tree)
    recorder = _Mutator(None)
    recorder.visit(tree)
    return [(index, line) for index, line in enumerate(recorder.sites) if line in body_lines]


def run_tests(repo: Path, targets: list[str], timeout: float = 900) -> tuple[bool | None, float]:
    """True when every selected test passed."""
    if not targets:
        return True, 0.0  # No assertions; never substitute pytest's default full suite.
    started = time.monotonic()
    args = [] if targets == ["ALL"] else targets
    command = [*pytest_argv(repo), "-x", "-o", "addopts="]
    result = run([*command, *args], repo, timeout, candidate_env(repo))
    if result.returncode not in (0, 1, 124) and args:
        # A fault can change or break collection (for example tests parametrised from the
        # selector), so the requested ids vanish. Rerun their modules: a collection error
        # there fails CI just as a failing test does.
        modules = list(dict.fromkeys(target.split("::", 1)[0] for target in args))
        result = run([*command, *modules], repo, timeout, candidate_env(repo))
        if result.returncode == 2:
            return False, time.monotonic() - started
    return (
        result.returncode == 0 if result.returncode in (0, 1) else None
    ), time.monotonic() - started


def ci_quality(
    repo: Path,
    seed: int,
    mutants: int = 10,
    *,
    changed_paths: list[str] | None = None,
    include_observations: bool = False,
) -> dict:
    """Selector cost per file and safety against planted bugs the suite itself catches."""
    report: dict = {"selector": "missing"}
    collected = collect(repo, ["ALL"])
    all_tests = collected or []
    report["collection_complete"] = collected is not None and bool(all_tests)
    report["tests_total"] = len(all_tests)
    if not (repo / "ci.json").exists():
        return report
    covered, info = coverage_by_test(repo)
    report["coverage_run"] = info
    # Parametrised ids built from run-dependent values (timestamps, addresses) differ between
    # pytest processes; such a test is identified by its function so evidence stays comparable.
    executed = {test for lines in covered.values() for tests in lines.values() for test in tests}
    unstable = sorted({test.split("[", 1)[0] for test in executed ^ set(all_tests) if "[" in test})
    if unstable:
        canon = set(unstable)

        def stable(test):
            base = test.split("[", 1)[0]
            return base if base in canon else test

        all_tests = list(dict.fromkeys(stable(test) for test in all_tests))
        covered = {
            path: {line: {stable(test) for test in tests} for line, tests in lines.items()}
            for path, lines in covered.items()
        }
        report["unstable_test_ids"] = {
            "functions": unstable[:20],
            "functions_total": len(unstable),
            "note": "Parametrised ids differ between runs; each is identified by its function. "
            "Build parameter ids from fixed values (for example gzip.compress(data, mtime=0)).",
        }
    else:

        def stable(test):
            return test

    report["tests_collected"] = report["tests_total"]
    per_file, selections, observations = [], {}, []
    collection_cache = {("ALL",): all_tests}

    def test_ids(targets):
        key = tuple(targets)
        if key not in collection_cache:
            ids = collect(repo, targets)
            collection_cache[key] = (
                None if ids is None else list(dict.fromkeys(stable(test) for test in ids))
            )
        return collection_cache[key]

    selector_seconds = []
    for path in production_files(repo):
        targets, seconds = select(repo, [path])
        selector_seconds.append(seconds)
        if targets is None:
            per_file.append({"file": path, "selector_failed": True})
            continue
        ids = test_ids(targets)
        if ids is None or not set(ids) <= set(all_tests):
            per_file.append({"file": path, "selector_failed": True})
            continue
        selections[path] = (targets, set(ids))
        executing = set().union(*covered.get(path, {}).values()) if covered.get(path) else set()
        if not executing <= set(all_tests):
            report["collection_complete"] = False
        observations.append({"path": path, "executing": sorted(executing), "selected": sorted(ids)})
        per_file.append(
            {
                "file": path,
                "all": targets == ["ALL"],
                "selected": len(ids),
                "executing": len(executing),
                "executing_selected": len(executing & set(ids)),
            }
        )
    total = max(len(all_tests), 1)
    measured = [row for row in per_file if not row.get("selector_failed")]
    report.update(
        {
            "selector": "ok" if measured else "failed",
            "selector_failures": sum(bool(row.get("selector_failed")) for row in per_file),
            "selector_max_seconds": round(max(selector_seconds, default=0), 2),
            "mean_selected_fraction": round(
                sum(r["selected"] for r in measured) / total / len(measured), 4
            )
            if measured
            else None,
            "production_files_measured": len(measured),
            "mean_selected_tests": sum(r["selected"] for r in measured) / len(measured)
            if measured
            else None,
            "omitted_execution_pairs": sum(
                r["executing"] - r["executing_selected"] for r in measured
            ),
            "selection_completeness": "observed_gap"
            if any(r["executing"] != r["executing_selected"] for r in measured)
            else "observed_complete"
            if measured
            else "unverified",
            "mean_executing_fraction": round(
                sum(r["executing"] for r in measured) / total / len(measured), 4
            )
            if measured
            else None,
            "files_selecting_all": sum(r["all"] for r in measured),
            "executing_recall": round(
                sum(r["executing_selected"] for r in measured)
                / max(sum(r["executing"] for r in measured), 1),
                4,
            ),
            "per_file": per_file,
        }
    )
    report["mutants"] = plant_bugs(repo, covered, selections, seed, mutants)
    if changed_paths is not None:
        targets, seconds = select(repo, changed_paths)
        ids = test_ids(targets) if targets is not None else None
        required = set().union(
            *(set(row["executing"]) for row in observations if row["path"] in changed_paths)
        )
        observed_paths = {row["path"] for row in observations if row["executing"]}
        for path in changed_paths:
            if path.startswith("docs/") or path.endswith((".md", ".rst")):
                continue
            if PurePosixPath(path).name == "conftest.py":  # Its fixtures reach the whole subtree.
                base = PurePosixPath(path).parent.as_posix()
                required.update(
                    test
                    for test in all_tests
                    if base == "." or test.split("::")[0].startswith(base + "/")
                )
            elif is_test_path(path):
                required.update(test for test in all_tests if test.split("::")[0] == path)
            elif path not in observed_paths and not path.endswith((".md", ".rst")):
                required.update(all_tests)  # Shared/configuration/unresolved inputs widen.
        omitted = required - set(ids or [])
        report["change_selection"] = {
            "changed_paths": changed_paths,
            "selected_tests": sorted(ids or []),
            "required_tests": sorted(required),
            "omitted_tests": sorted(omitted),
            "status": "incomplete"
            if ids is None or not set(ids) <= set(all_tests)
            else "failed"
            if omitted
            else "passed",
            "selector_seconds": seconds,
        }
    if include_observations:
        report["observations"] = {
            "tests": all_tests,
            "files": observations,
            "coverage_present": bool(covered),
        }
    return report


def plant_bugs(repo, covered, selections, seed: int, count: int) -> dict:
    rng = random.Random(seed)
    sites = []
    for path, lines in covered.items():
        if path not in selections:
            continue
        source = (repo / path).read_text(encoding="utf-8")
        try:
            for index, line in candidate_mutants(source):
                if lines.get(line):
                    sites.append((path, index, line))
        except SyntaxError:
            continue
    rng.shuffle(sites)
    results = {
        "planted": 0,
        "caught_by_suite": 0,
        "missed_by_selection": 0,
        "unverified": 0,
        "details": [],
    }
    for path, index, line in sites[: count * 3]:
        if results["planted"] >= count:
            break
        file = repo / path
        original = file.read_text(encoding="utf-8")
        mutated = mutate(original, index)
        if mutated is None or mutated[0] == original:
            continue
        executing = sorted(covered[path].get(line, set()))
        try:
            file.write_text(mutated[0], encoding="utf-8")
            passed, _ = run_tests(repo, executing)
            row = {
                "file": path,
                "line": line,
                "kind": mutated[1][0],
                "caught_by_suite": passed is False,
            }
            if passed is None:
                row["verification_error"] = True
            if passed is False:
                targets, _ = selections[path]
                selected_pass, _ = run_tests(repo, targets)
                row["caught_by_selection"] = None if selected_pass is None else not selected_pass
                if selected_pass is None:
                    row["verification_error"] = True
        finally:
            file.write_text(original, encoding="utf-8")
        results["planted"] += 1
        results["caught_by_suite"] += row["caught_by_suite"]
        results["missed_by_selection"] += row.get("caught_by_selection") is False
        results["unverified"] += bool(row.get("verification_error"))
        results["details"].append(row)
    return results
