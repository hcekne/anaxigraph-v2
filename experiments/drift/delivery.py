"""Immutable local artifact identities and executable previous-release recovery.

No hosting provider is contacted. These are local build/deploy ledger simulations,
with real byte identities and real execution of the recovered previous source.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

from . import oracle
from .fixture import digest


def imports(root, path):
    package = path.split("/")[:-1]
    found = set()
    for node in ast.walk(ast.parse((root / path).read_text())):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            prefix = package[: len(package) - node.level + 1] if node.level else []
            base = [*prefix, *(node.module.split(".") if node.module else [])]
            modules = [".".join(base), *(".".join([*base, alias.name]) for alias in node.names)]
        else:
            continue
        for module in modules:
            target = module.replace(".", "/")
            found.update((target + ".py", target + "/__init__.py"))
    parent = Path(path).parent
    while parent != Path("."):
        found.add((parent / "__init__.py").as_posix())
        parent = parent.parent
    return found


def artifacts(root, spec, runtime):
    files = oracle.snapshot(root)
    providers = spec["ids"]["providers"]
    result = {}
    for name in [*providers, "host"]:
        if name == "host":
            inputs = {
                key
                for key in files
                if key.startswith(("config/", "contracts/", "events/", "storage/"))
                or key in {"platform.lock", "release.json"}
            }
            pending = [
                "solution.py",
                *(key for key in files if key.startswith("integrations/") and key.endswith(".py")),
            ]
        else:
            inputs = {
                key for key in ("platform.lock", f"contracts/providers/{name}.json") if key in files
            }
            pending = [f"providers/{name}.py"]
        while pending:
            path = pending.pop()
            if path in inputs or path not in files:
                continue
            inputs.add(path)
            pending.extend(target for target in imports(root, path) if target in files)
        material = {
            "component": name,
            "inputs": {key: files[key] for key in sorted(inputs) if key in files},
            "runtime": runtime,
        }
        result[name] = {"digest": digest(material), **material}
    return result


def receipt(root, spec, selected, runtime, previous):
    current = artifacts(root, spec, runtime)
    return {
        "candidate_digest": digest(oracle.snapshot(root)),
        "check_ids": sorted(selected),
        "runtime": runtime,
        "artifact_digests": {key: value["digest"] for key, value in current.items()},
        "rollback": {key: value["digest"] for key, value in (previous or {}).items()},
    }


def verify(
    root, spec, proposal, evidence, runtime, previous, *, recovery=None, recovery_digest=None
):
    current = artifacts(root, spec, runtime)
    expected = receipt(root, spec, proposal["selected"], runtime, previous)
    changed = sorted(
        key
        for key, value in current.items()
        if not previous or value["digest"] != previous[key]["digest"]
    )
    failures = []
    if evidence != expected:
        failures.append("artifact_or_verification_identity_mismatch")
    if set(changed) - set(proposal["deploy"]):
        failures.append("omitted_changed_artifact")
    if spec["rollout"] and (recovery is None or recovery_digest is None):
        failures.append("rollback_not_executed")
    if recovery is not None and (
        not recovery.get("unchanged")
        or recovery.get("candidate_digest") != recovery_digest
        or set(recovery.get("checks", {})) != set(oracle.checks(spec))
        or not all(row["passed"] for row in recovery["checks"].values())
    ):
        failures.append("rollback_failed")
    return {
        "passed": not failures,
        "failures": failures,
        "required_deploy": changed,
        "proposed_deploy": proposal["deploy"],
        "tested_artifacts": expected["artifact_digests"],
        "rollback_artifacts": expected["rollback"],
        "recovery": recovery,
        "artifact_count": len(current),
        "synthetic_deployment_work": {
            "unit": "equal local artifact units; no hosted build/deploy duration measured",
            "selected": len(proposal["deploy"]),
            "required": len(changed),
            "total": len(current),
            "excess": sorted(set(proposal["deploy"]) - set(changed)),
            "work_avoided_credit": len(current) - len(proposal["deploy"]) if not failures else None,
        },
        "deployment_kind": "Local ledger simulation; no hosted rollout or production rollback was performed",
    }


def preserve(root: Path, destination: Path):
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        root,
        destination,
        ignore=shutil.ignore_patterns(".git", ".anaxigraph-v2", "__pycache__", "submission.json"),
        symlinks=True,
    )
