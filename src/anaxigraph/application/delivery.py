"""CI-design evidence and declared change plans; never execute project tasks."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import PurePosixPath
from typing import Any

from anaxigraph.analysis.changes import changed_paths as comparison_paths
from anaxigraph.analysis.ci_context import component_context
from anaxigraph.analysis.ci_obligations import REPORT_PATH, audit_selection
from anaxigraph.analysis.ci_policy import plan_ci as select_checks
from anaxigraph.analysis.ci_policy import validate_policy
from anaxigraph.analysis.patterns import production_modules
from anaxigraph.analysis.portable import input_identity
from anaxigraph.application.architecture import package_context
from anaxigraph.indexing import capture_sources
from anaxigraph.indexing.ci_evidence import capture_ci_evidence, redacted_command
from anaxigraph.indexing.python import is_test

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


def policy(app: Any, graph) -> dict | None:
    if POLICY_PATH not in graph.snapshot.files:
        if (app.root / POLICY_PATH).exists():
            raise ValueError("CI policy is excluded from the snapshot; include it before planning")
        return None
    captured = capture_sources(app.root, [POLICY_PATH], graph.snapshot.files, maximum=1_000_000)
    try:
        return validate_policy(json.loads(captured[POLICY_PATH]))
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid CI policy: {error}") from error


def _policy_context(app: Any, graph) -> dict:
    try:
        declared = policy(app, graph)
    except ValueError as error:
        return {"status": "invalid", "reason": str(error)[:300]}
    if declared is None:
        return {"status": "not_configured", "path": POLICY_PATH}
    targets = [
        {**item, "command": redacted_command(item["command"])} for item in declared["targets"][:100]
    ]
    return {
        "status": "declared",
        "path": POLICY_PATH,
        "fingerprint": graph.snapshot.files[POLICY_PATH],
        "targets": targets,
        "targets_total": len(declared["targets"]),
        "targets_truncated": len(declared["targets"]) > 100,
        **{key: value for key, value in declared.items() if key not in {"version", "targets"}},
    }


def context(app: Any, graph, pipeline_paths: list[str] | None = None) -> dict:
    selected = tuple(sorted(set(pipeline_paths or [])))
    cached = getattr(app, "_delivery_context_cache", None)
    if cached is None or cached[0] is not graph or cached[1] != selected:
        captured = capture_ci_evidence(app.root, graph.snapshot.files, list(selected) or None)
        captured = captured | {
            "components": component_context(graph),
            "package_context": package_context(app, graph),
            "ci_policy": _policy_context(app, graph),
            "test_selection": selection_evidence(app, graph),
        }
        cached = (graph, selected, captured)
        app._delivery_context_cache = cached
    return cached[2]


def _scope(body: dict) -> dict:
    paths = body.get("request_options", {}).get("pipeline_paths") or []
    captured = body.get("delivery_coverage") or {}
    partial = captured.get("partial_evidence") is not False
    reviewed = len(captured.get("pipeline_ids", []))
    return {
        "pipeline_paths": [value[:240] for value in paths[:3]],
        "pipeline_paths_total": len(paths),
        "pipeline_paths_truncated": len(paths) > 3,
        "coverage": {
            "partial_evidence": partial,
            "pipeline_ids_total": reviewed,
            "pipelines_total": captured.get("pipelines_total", reviewed),
        },
        "scope_status": "filtered"
        if paths
        else ("partial_evidence" if partial else "captured_declarations"),
    }


def advice(app: Any, graph, changed_paths=None) -> dict:
    result = _review_advice(app, graph)
    evidence = selection_evidence(app, graph, changed_paths)
    if evidence["status"] != "not_supplied":
        result["selection_evidence"] = evidence
    return result


def _review_advice(app: Any, graph) -> dict:
    rows = app.store.reviews(str(app.root), "delivery", include_requests=False, limit=1)
    if not rows:
        return {"status": "not_reviewed", "workflow": "ci", "suggestions": []}
    row = rows[0]
    scope = _scope(row["result"])
    try:
        request = app._rebuild_request(graph, "delivery", row["result"])
    except ValueError:
        request = None
    if request is None or input_identity(request) != row["result"].get("input_identity"):
        return {"status": "stale", "workflow": "ci", "suggestions": [], **scope}
    findings = row["result"]["result"]["recommendations"]
    return {
        "status": "compatible_design_review",
        **scope,
        "review_id": row["id"],
        "basis": "Matching CI, component inventory/topology and policy inputs; no checks were executed or reused.",
        "suggestions": [
            {
                "title": item["title"],
                "next_step": item["next_steps"][0],
                "broaden_when": item["broaden_when"][:1],
                "evidence": item["evidence"][:1],
                "expand": "review:" + row["id"],
            }
            for item in findings[:1]
        ],
        "workflow": "ci",
    }


def _git(root, *arguments: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError("CI comparison could not resolve the supplied local Git base")
    if len(result.stdout) > 2_000_000:
        raise ValueError(
            "CI change listing exceeds its budget; supply a narrower explicit change list"
        )
    return result.stdout


def _git_changed(app, graph, base_ref) -> tuple[list[str], dict]:
    if not isinstance(base_ref, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9/._-]{0,199}", base_ref
    ):
        raise ValueError("CI base_ref must be a local Git revision name or commit")
    resolved = _git(app.root, "rev-parse", "--verify", base_ref + "^{commit}").decode().strip()
    paths = _git(app.root, "diff", "--name-only", "--no-renames", "-z", resolved, "--")
    others = _git(app.root, "ls-files", "--others", "--exclude-standard", "-z")
    values = {value.decode() for value in paths.split(b"\0") if value}
    values.update(
        value.decode()
        for value in others.split(b"\0")
        if value and value.decode() in graph.snapshot.files
    )
    return sorted(values), {
        "basis": "local-git-comparison",
        "base_ref": base_ref,
        "base_commit": resolved,
    }


def _changed(app, graph, base_ref, base_snapshot_id, changed_paths) -> tuple[list[str], dict]:
    if sum(value is not None for value in (base_ref, base_snapshot_id, changed_paths)) > 1:
        raise ValueError("Select one CI comparison base or an explicit changed-path list")
    if changed_paths is not None:
        return changed_paths, {"basis": "caller-supplied-paths", "completeness": "caller-reported"}
    if base_ref is not None:
        return _git_changed(app, graph, base_ref)
    selected = base_snapshot_id or app.store.stats(str(app.root)).get("pinned_snapshot_id")
    previous = app.store.load_graph(str(app.root), selected) if selected else None
    if previous is None:
        raise ValueError(
            "CI planning needs an explicit base, pinned task baseline or changed paths"
        )
    before, after = previous.snapshot.files, graph.snapshot.files
    return sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    ), {
        "basis": "explicit-snapshot" if base_snapshot_id else "pinned-change-baseline",
        "base_snapshot_id": selected,
    }


def plan(
    app: Any,
    *,
    base_ref=None,
    base_snapshot_id=None,
    changed_paths=None,
    cadence="change",
    runtime_context=None,
) -> dict:
    graph = app._current()
    app._require_live(graph)
    declared = policy(app, graph)
    if declared is None:
        return {
            "status": "not_configured",
            "execution_ready": False,
            "checks_executed": False,
            "policy_path": POLICY_PATH,
            "selected_targets": [],
            "layers": [],
            "uncovered_paths": [],
            "next_step": "Run the CI/CD review, then declare accepted task inputs and dependencies in the policy file.",
            "selection_evidence": selection_evidence(app, graph, changed_paths, runtime_context),
        }
    paths, comparison = _changed(app, graph, base_ref, base_snapshot_id, changed_paths)
    result = select_checks(
        declared, paths, graph.snapshot.files, cadence=cadence, runtime_context=runtime_context
    )
    evidence = _selection_validation(app, graph, paths, runtime_context)
    if _validation_status([], evidence) in {"blocked", "incomplete"}:
        result |= {"execution_ready": False, "status": "selection_review_required"}
    app._require_live(graph)
    return result | {
        "comparison": comparison,
        "changed_paths": paths,
        "candidate_snapshot_id": graph.snapshot.id,
        "candidate_commit": graph.snapshot.revision,
        "policy_fingerprint": graph.snapshot.files[POLICY_PATH],
        "checks_executed": False,
        "selection_evidence": evidence,
        "assurance": "Declared policy and identified changes; not proof of behavioral compatibility or successful deployment.",
    }


def advisory(app: Any, **planargs: Any) -> dict:
    """Keep missing CI evidence actionable without blocking the surrounding task brief."""
    try:
        return plan(app, **planargs)
    except ValueError as error:
        return {
            "status": "unresolved",
            "execution_ready": False,
            "checks_executed": False,
            "reason": str(error),
        }
