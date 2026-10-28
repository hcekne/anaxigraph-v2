"""External behavioral, independence and CI-safety checks, never candidate test counts.

Architecture dimensions remain separate: functional success is required before a
design can qualify. There is no reward for file, package or line counts.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import copy
import hashlib
import importlib
import json
import random
import subprocess
import sys
import time
from pathlib import Path

try:
    from .scenarios import CHECKS, SCENARIOS, oracle
except ImportError:
    from scenarios import CHECKS, SCENARIOS, oracle


def source_files(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text()
        for path in root.rglob("*.py")
        if not any(part.startswith(".") for part in path.relative_to(root).parts)
        and "tests" not in path.relative_to(root).parts
        and path.name != "ci_select.py"
        and root in path.resolve().parents
    }


def structure(root: Path) -> dict:
    manifest = json.loads((root / "components.json").read_text())
    sources = source_files(root)
    modules = {
        path.removesuffix(".py").replace("/", ".").removesuffix(".__init__"): path
        for path in sources
    }
    graph, violations = {}, []
    for name, path in modules.items():
        tree, dependencies = ast.parse(sources[path]), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                dependencies.update(alias.name for alias in node.names if alias.name in modules)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    package = name if path.endswith("/__init__.py") else name.rpartition(".")[0]
                    parts = package.split(".") if package else []
                    if node.level > 1:
                        parts = parts[: -(node.level - 1)]
                    base = ".".join(parts + ([base] if base else []))
                if base in modules and base != name:
                    dependencies.add(base)
                dependencies.update(
                    f"{base}.{alias.name}"
                    for alias in node.names
                    if f"{base}.{alias.name}" in modules
                )
        graph[name] = dependencies

    def closure(name, visiting=None):
        visiting = set() if visiting is None else visiting
        if name in visiting:
            return {name}
        result = {name}
        # Importing pkg.part first executes each parent package initializer. Its
        # side effects/dependencies remain inputs even without an explicit import.
        parents = {".".join(name.split(".")[:index]) for index in range(1, len(name.split(".")))}
        for dependency in graph.get(name, set()) | (parents & modules.keys()):
            result |= closure(dependency, visiting | {name})
        return result

    entries, inputs, reachable = {}, {}, {}
    for component in ("compute", "delivery"):
        module, symbol = manifest["components"][component].split(":")
        if module not in modules or not symbol.isidentifier():
            raise ValueError("Entrypoint must name a local public callable")
        if symbol.startswith("_"):
            violations.append(f"{component} entrypoint is private")
        entries[component] = [module, symbol]
        inputs[component] = set(manifest["inputs"][component])
        reachable[component] = {modules[name] for name in closure(module)}
        if reachable[component] - inputs[component]:
            violations.append(f"{component} has undeclared transitive source inputs")
    # Co-located ordinary functions are allowed. Importability and direct cross-entrypoint
    # calls, rather than a prescribed package count or directory tree, establish isolation.
    for component, (module, symbol) in entries.items():
        other = entries["delivery" if component == "compute" else "compute"]
        if module != other[0] and modules[other[0]] in reachable[component]:
            violations.append(f"{component} imports neighboring implementation")
        if module == other[0]:
            tree = ast.parse(sources[modules[module]])
            functions = {
                node.name: node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            visited = set()

            def calls(name):
                if name in visited or name not in functions:
                    return
                visited.add(name)
                for node in ast.walk(functions[name]):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                        if node.func.id == other[1]:
                            violations.append(f"{component} calls neighboring implementation")
                        calls(node.func.id)

            calls(symbol)
    return {
        "entries": entries,
        "inputs": {key: sorted(value) for key, value in inputs.items()},
        "reachable": {key: sorted(value) for key, value in reachable.items()},
        "violations": sorted(set(violations)),
    }


def independent_import(root: Path, facts: dict, component: str, request: dict) -> None:
    own = facts["entries"][component][0]
    other = facts["entries"]["delivery" if component == "compute" else "compute"][0]
    original = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        resolved = (
            importlib.util.resolve_name("." * level + name, globals.get("__package__"))
            if level
            else name
        )
        names = {resolved} | {resolved + "." + item for item in (fromlist or ()) if item != "*"}
        if other != own and any(value == other or value.startswith(other + ".") for value in names):
            raise ImportError("neighbor deliberately unavailable")
        return original(name, globals, locals, fromlist, level)

    local_modules = {
        path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
        for path in source_files(root)
    }
    local_modules |= {
        ".".join(name.split(".")[:index])
        for name in list(local_modules)
        for index in range(1, len(name.split(".")))
    }
    for module in local_modules:
        sys.modules.pop(module, None)
    builtins.__import__ = guarded
    try:
        value = importlib.import_module(own)
        callable_ = getattr(value, facts["entries"][component][1])
        if not callable(callable_):
            raise TypeError("entrypoint is not callable")
        if component == "compute":
            assert type(callable_(request, {"multiplier": 2})) is int
        else:
            assert callable_("independent", 7, lambda *args: True)["accepted"] is True
    finally:
        builtins.__import__ = original


def behavior(root: Path, name: str, stage: int, random_seed: int) -> dict:
    """Runs in a fresh subprocess; hidden samples and fake effects remain external."""
    sys.path.insert(0, str(root))
    application = importlib.import_module("solution")
    facts = structure(root)
    functions = {
        key: getattr(importlib.import_module(entry[0]), entry[1])
        for key, entry in facts["entries"].items()
    }
    rng, failures, tested = random.Random(random_seed), [], 0
    spec = SCENARIOS[name]

    def check(label, action):
        nonlocal tested
        tested += 1
        try:
            action()
        except Exception as error:
            failures.append(
                {"check": label, "error": type(error).__name__, "detail": str(error)[:180]}
            )

    def sample(index):
        values = [0, 1, 1023, 1024, 1025, 10**14]
        request = {"reference": f"case-{index}"}
        for field in spec["fields"]:
            request[field] = values[index] if index < len(values) else rng.randrange(10**6)
        if stage >= 1:
            request[spec["extension"]] = (
                rng.randrange(1, 4) if name == "dispatch" else rng.randrange(10**5)
            )
        return request

    def exercise(request, settings, provider_result, error=None, expected_error=None):
        calls, before = [], copy.deepcopy((request, settings))

        def provider(reference, value):
            calls.append([reference, value])
            if error:
                raise error("injected")
            return provider_result

        if expected_error:
            try:
                application.execute(request, settings, provider)
            except expected_error:
                assert not calls, "validation must precede external effects"
                return
            raise AssertionError("invalid behavior was accepted")
        expected = oracle(name, request, settings, stage)
        assert functions["compute"](request, settings) == expected, "component value contract"
        result = application.execute(request, settings, provider)
        accepted = (
            provider_result["ok"] if isinstance(provider_result, dict) else bool(provider_result)
        )
        expected_result = {
            "reference": request["reference"],
            "value": expected,
            "accepted": accepted,
        }
        if error is OSError:
            expected_result.update(accepted=False, error="unavailable")
        elif isinstance(provider_result, dict):
            expected_result["receipt"] = provider_result["receipt"]
        assert result == expected_result, "public response contract"
        assert calls == [[request["reference"], expected]], (
            "exactly one correctly valued side effect"
        )
        assert (request, settings) == before, "input mutation"
        calls.clear()
        assert functions["delivery"](request["reference"], expected, provider) == expected_result
        assert calls == [[request["reference"], expected]], "delivery side effect contract"

    for index in range(30):
        request, settings = sample(index), {"multiplier": rng.randrange(1, 8)}
        check(f"behavior-{index}", lambda r=request, s=settings: exercise(r, s, index % 2 == 0))
    for index, field in enumerate(spec["fields"]):
        for invalid in (-1, True, 2.5, None, "3"):
            request = sample(index)
            request[field] = invalid
            check(
                "invalid-number",
                lambda r=request: exercise(r, {"multiplier": 2}, True, expected_error=ValueError),
            )
    for reference in (None, "", 42):
        request = sample(0) | {"reference": reference}
        check(
            "invalid-reference",
            lambda r=request: exercise(r, {"multiplier": 2}, True, expected_error=ValueError),
        )
    for multiplier in (0, -1, True, "2", None):
        check(
            "invalid-settings",
            lambda m=multiplier: exercise(
                sample(0), {"multiplier": m}, True, expected_error=ValueError
            ),
        )
    if stage >= 1:
        for invalid in (-1, True, 1.5, "1", None) + ((0, 4) if name == "dispatch" else ()):
            request = sample(2) | {spec["extension"]: invalid}
            check(
                "invalid-extension",
                lambda r=request: exercise(r, {"multiplier": 2}, True, expected_error=ValueError),
            )
        legacy = sample(4)
        legacy.pop(spec["extension"], None)
        check("legacy-default-extension", lambda: exercise(legacy, {"multiplier": 2}, True))
    check(
        "provider-unavailable", lambda: exercise(sample(2), {"multiplier": 2}, False, error=OSError)
    )

    def unexpected_failure():
        calls = []

        def provider(*args):
            calls.append(args)
            raise RuntimeError("unexpected provider failure")

        try:
            application.execute(sample(2), {"multiplier": 2}, provider)
        except RuntimeError:
            assert len(calls) == 1
            return
        raise AssertionError("unexpected provider failures must propagate")

    check("unexpected-provider-error", unexpected_failure)
    if stage >= 2:
        for accepted in (False, True):
            check(
                "provider-v2",
                lambda a=accepted: exercise(
                    sample(3), {"multiplier": 2}, {"ok": a, "receipt": "R-987"}
                ),
            )
    for component in ("compute", "delivery"):
        check(
            f"independent-{component}",
            lambda c=component: independent_import(root, facts, c, sample(1)),
        )

    # Configuration changes retain the signature but affect both numerical and side-effect contracts.
    def default_settings():
        request, calls = sample(3), []
        settings = json.loads((root / "settings.json").read_text())
        if stage >= 3:
            assert settings["multiplier"] == 3
            assert json.loads((root / "schema.json").read_text()) == {
                "version": 2,
                "multiplier_semantics": "all_values",
            }
        else:
            assert settings == {"multiplier": 2}, "preserve existing default configuration behavior"
            assert json.loads((root / "schema.json").read_text()) == {"version": 1}
        expected = oracle(name, request, settings, stage)
        result = application.execute(request, dependency=lambda *args: calls.append(args) or True)
        assert result["value"] == expected and calls == [(request["reference"], expected)]

    check("shared-configuration-semantics", default_settings)
    return {"checks": tested, "failures": failures, "structure": facts}


def run_check(root: Path, check: str, timeout: float = 30, forbidden: str | None = None) -> dict:
    started = time.monotonic()
    path = root / "tests" / f"test_{check}.py"
    if not path.is_file():
        return {"passed": False, "elapsed_seconds": 0, "error": "required check missing"}
    bootstrap = """import builtins, importlib.util, runpy, sys
root, path, forbidden = sys.argv[1:]
sys.path.insert(0, root)
sys.argv = [path]
original = builtins.__import__
def guarded(name, globals=None, locals=None, fromlist=(), level=0):
    resolved = importlib.util.resolve_name('.' * level + name, globals.get('__package__')) if level else name
    names = {resolved} | {resolved + '.' + item for item in (fromlist or ()) if item != '*'}
    if forbidden and any(value == forbidden or value.startswith(forbidden + '.') for value in names):
        raise ImportError('neighbor deliberately unavailable')
    return original(name, globals, locals, fromlist, level)
builtins.__import__ = guarded
runpy.run_path(path, run_name='__main__')
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", bootstrap, str(root), str(path), forbidden or ""],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "error": "candidate check timed out",
        }
    # unittest's empty/no-test success is not accepted as useful independent evidence.
    output = result.stdout + result.stderr
    return {
        "passed": result.returncode == 0 and "Ran " in output and "Ran 0 tests" not in output,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "output": output[-2000:],
    }


def evaluate(root: Path, name: str, stage: int, *, random_seed: int, changed_paths=()) -> dict:
    root = root.resolve()
    started = time.monotonic()

    def fingerprint():
        return {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file()
            and not any(
                part.startswith(".") or part == "__pycache__"
                for part in path.relative_to(root).parts
            )
            and root in path.resolve().parents
        }

    before = fingerprint()
    try:
        worker = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
                str(root),
                name,
                str(stage),
                str(random_seed),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {"qualified": False, "error": "hidden behavioral check timed out"}
    try:
        hidden = json.loads(worker.stdout.splitlines()[-1]) if worker.returncode == 0 else {}
    except (ValueError, IndexError):
        hidden = {}
    if not hidden:
        return {
            "qualified": False,
            "error": "hidden evaluator failed",
            "detail": worker.stderr[-1500:],
        }
    facts = hidden["structure"]
    cases = []
    for component in ("compute", "delivery"):
        for path in facts["inputs"][component]:
            owners = {key for key, inputs in facts["reachable"].items() if path in inputs}
            cases.append(("implementation", [path], [], owners | {"integration"}))
    cases += [
        ("compute-semantic-contract", [], ["compute"], set(CHECKS)),
        ("delivery-semantic-contract", [], ["delivery"], {"delivery", "integration"}),
    ]
    for path in (
        "settings.json",
        "schema.json",
        "pyproject.toml",
        "components.json",
        "ci_select.py",
        "unowned/new.data",
    ):
        cases.append(("shared-or-unknown", [path], [], set(CHECKS)))
    if changed_paths:
        paths = list(changed_paths)
        expected = {"integration"}
        for path in paths:
            owners = {key for key, inputs in facts["reachable"].items() if path in inputs}
            expected |= owners or set(CHECKS)
        cases.append(
            (
                "actual-patch",
                paths,
                ["compute"] if stage == 1 else [],
                expected | (set(CHECKS) if stage == 1 else set()),
            )
        )
    plans = []
    for label, paths, contracts, required in cases:
        try:
            command = subprocess.run(
                [sys.executable, str(root / "ci_select.py")],
                input=json.dumps({"changed_paths": paths, "changed_contracts": contracts}),
                capture_output=True,
                text=True,
                cwd=root,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            plans.append(
                {
                    "case": label,
                    "changed_paths": paths,
                    "changed_contracts": contracts,
                    "required": sorted(required),
                    "selected": [],
                    "safe": False,
                    "error": "CI selector timed out",
                }
            )
            continue
        try:
            selected = json.loads(command.stdout)
            valid = isinstance(selected, list) and all(
                isinstance(item, str) and item in CHECKS for item in selected
            )
        except ValueError:
            selected, valid = [], False
        if not valid:
            selected = []
        safe = valid and command.returncode == 0 and required <= set(selected)
        plans.append(
            {
                "case": label,
                "changed_paths": paths,
                "changed_contracts": contracts,
                "required": sorted(required),
                "selected": selected,
                "safe": safe,
            }
        )
    checks = {}
    for check in CHECKS:
        forbidden = None
        if check != "integration":
            own = facts["entries"][check][0]
            other = facts["entries"]["delivery" if check == "compute" else "compute"][0]
            forbidden = other if own != other else None
        checks[check] = run_check(root, check, forbidden=forbidden)
    behavior_failures = [
        failure for failure in hidden["failures"] if not failure["check"].startswith("independent-")
    ]
    independence_failures = [
        failure for failure in hidden["failures"] if failure["check"].startswith("independent-")
    ]
    safe = all(plan["safe"] for plan in plans)
    passing = not behavior_failures and all(check["passed"] for check in checks.values())
    unchanged = fingerprint() == before
    documented = (root / "contracts.md").is_file() and bool(
        (root / "contracts.md").read_text().strip()
    )
    return {
        "qualified": passing
        and safe
        and unchanged
        and documented
        and not facts["violations"]
        and not independence_failures,
        "behavior_passed": not behavior_failures,
        "checkout_unchanged_during_checks": unchanged,
        "contract_documentation_present": documented,
        "ci_safe": safe,
        "independent_components": 2 - len(independence_failures),
        "boundary_violations": facts["violations"],
        "hidden": hidden,
        "ci_cases": plans,
        "candidate_checks": checks,
        "actual_evaluator_seconds": round(time.monotonic() - started, 6),
        "actual_candidate_check_seconds": sum(row["elapsed_seconds"] for row in checks.values()),
        "synthetic_ci_cost": {
            "basis": "equal unit weights, not measured CI runtime or parallel wall time",
            "weights": dict.fromkeys(CHECKS, 1),
            "implementation_cases": [
                {
                    "changed_paths": plan["changed_paths"],
                    "selected_units": len(set(plan["selected"])),
                }
                for plan in plans
                if plan["case"] == "implementation" and plan["safe"]
            ],
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("scenario", choices=SCENARIOS)
    parser.add_argument("stage", type=int)
    parser.add_argument("random_seed", type=int)
    args = parser.parse_args()
    print(json.dumps(behavior(args.worker.resolve(), args.scenario, args.stage, args.random_seed)))
