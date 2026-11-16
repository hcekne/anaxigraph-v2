"""CI-design evidence and supplied selector evidence; never execute project tasks."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from anaxigraph.analysis.changes import changed_paths as comparison_paths
from anaxigraph.analysis.patterns import production_modules
from anaxigraph.indexing import capture_sources
from anaxigraph.indexing.python import is_test

from .ci_obligations import REPORT_PATH, audit_selection

POLICY_PATH = "anaxigraph/ci-policy.json"


def selection_evidence(
    app: Any,
    graph,
    changed_paths=None,
    runtime_context=None,
    *,
    require_suite=False,
    actual_changed_paths=None,
) -> dict:
    if REPORT_PATH not in graph.snapshot.files:
        if (app.root / REPORT_PATH).exists():
            return {"status": "invalid", "reason": "CI evidence is excluded from the snapshot."}
        return audit_selection(None, graph.snapshot.files)
    try:
        source = capture_sources(app.root, [REPORT_PATH], graph.snapshot.files, maximum=1_000_000)
        value = json.loads(source[REPORT_PATH])
        if value is None:
            raise ValueError("Present CI evidence must be a version 1 object, not null")
        scope = (
            [path for path in changed_paths if path != REPORT_PATH]
            if changed_paths is not None
            else None
        )
        return audit_selection(
            value,
            graph.snapshot.files,
            runtime_context,
            scope,
            require_suite=require_suite,
            actual_changed_paths=actual_changed_paths,
        ) | {
            "report_path": REPORT_PATH,
            "report_fingerprint": graph.snapshot.files[REPORT_PATH],
        }
    except (OSError, ValueError, UnicodeError, RecursionError) as error:
        return {"status": "invalid", "reason": str(error)[:300], "checks_executed": False}


def _selection_required(graph, previous, paths, explicit=False):
    configured = {"ci.json", POLICY_PATH}.intersection(graph.snapshot.files)
    if previous is not None:
        configured.update({"ci.json", POLICY_PATH}.intersection(previous.snapshot.files))
    relevant = any(
        not path.startswith("docs/") and PurePosixPath(path).suffix.lower() != ".md"
        for path in paths
        if path != REPORT_PATH
    )
    return explicit or bool(configured and relevant)


def _production_paths(graph):
    return {node.path for node in production_modules(graph)} | {
        path
        for path in graph.snapshot.files
        if PurePosixPath(path).suffix in {".js", ".jsx", ".ts", ".tsx", ".sql", ".loom"}
        and not is_test(path)
    }


def _validation_status(checks, evidence):
    if any(check["status"] == "failed" for check in checks):
        return "blocked"
    if evidence["status"] in {"reported_gap", "reported_fault_miss", "reported_failed_suite"}:
        return "blocked"
    if evidence["required"] and not evidence["ready"]:
        return "incomplete"
    if not checks:
        return "unchecked"
    return (
        "reported-passed" if all(check["status"] == "passed" for check in checks) else "incomplete"
    )


def _selection_validation(app, graph, paths, runtime, previous=None, explicit=False):
    required = _selection_required(graph, previous, paths, explicit)
    production = _production_paths(graph)
    scope = sorted(production if required else production.intersection(paths))
    evidence = selection_evidence(
        app,
        graph,
        scope,
        runtime,
        require_suite=required,
        actual_changed_paths=paths if required else None,
    )
    if required and evidence.get("faults", {}).get("outside_scope_missed"):
        evidence["status"] = "reported_fault_miss"
    ready = (
        evidence["status"] == "reported_passed"
        and evidence.get("suite_status") == "reported_passed"
    )
    return evidence | {"required": required, "ready": bool(required and ready)}


def validation(app, graph, previous, checks, runtime_context=None, require_ci_evidence=False):
    """Require externally collected selector evidence without executing project code."""
    paths = comparison_paths(graph, previous)
    evidence = _selection_validation(
        app, graph, paths, runtime_context, previous, require_ci_evidence
    )
    status = _validation_status(checks, evidence)
    return {
        "ci_selection": evidence,
        "validation_status": status,
        "behavioral_status": {"blocked": "failed", "incomplete": "unresolved"}.get(status, status),
        "completion_allowed": status == "reported-passed",
    }


def validate_change(
    app, receipts=None, baseline_id=None, *, runtime_context=None, require_ci_evidence=False
):
    """Apply the retired selector experiment's gate to normal product validation."""
    result = app.validate_change(receipts, baseline_id)
    graph = app._live()
    previous = (
        app.store.load_graph(str(app.root), result["baseline_id"])
        if result["baseline_id"]
        else None
    )
    return result | validation(
        app, graph, previous, result["checks"], runtime_context, require_ci_evidence
    )
