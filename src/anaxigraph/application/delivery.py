"""CI-design evidence and declared change plans; never execute project tasks."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from anaxigraph.analysis.ci_context import component_context
from anaxigraph.analysis.ci_policy import plan_ci as select_checks
from anaxigraph.analysis.ci_policy import validate_policy
from anaxigraph.analysis.portable import input_identity
from anaxigraph.application.architecture import package_context
from anaxigraph.indexing import capture_sources
from anaxigraph.indexing.ci_evidence import capture_ci_evidence, redacted_command

POLICY_PATH = "anaxigraph/ci-policy.json"


def _constant(value):
    raise ValueError(f"CI policy contains non-finite value {value}")


def policy(app: Any, graph) -> dict | None:
    if POLICY_PATH not in graph.snapshot.files:
        if (app.root / POLICY_PATH).exists():
            raise ValueError("CI policy is excluded from the snapshot; include it before planning")
        return None
    captured = capture_sources(app.root, [POLICY_PATH], graph.snapshot.files, maximum=1_000_000)
    try:
        return validate_policy(json.loads(captured[POLICY_PATH], parse_constant=_constant))
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
        "always": declared["always"],
        "fallback": declared["fallback"],
        "shared_inputs": declared["shared_inputs"],
        "ignored_inputs": declared["ignored_inputs"],
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
        }
        cached = (graph, selected, captured)
        app._delivery_context_cache = cached
    return cached[2]


def _scope(body: dict) -> dict:
    paths = body.get("request_options", {}).get("pipeline_paths") or []
    captured = body.get("delivery_coverage")
    coverage = None
    if isinstance(captured, dict):
        coverage = {key: value for key, value in captured.items() if type(value) in {bool, int}}
        for key in ("pipeline_ids", "incomplete_declarations"):
            values = captured.get(key, [])
            coverage[key] = [value[:240] for value in values[:3]]
            coverage[key + "_total"] = len(values)
            coverage[key + "_truncated"] = len(values) > 3
    return {
        "pipeline_paths": [value[:240] for value in paths[:3]],
        "pipeline_paths_total": len(paths),
        "pipeline_paths_truncated": len(paths) > 3,
        "coverage": coverage,
        "scope_status": "filtered"
        if paths
        else (
            "partial_evidence"
            if coverage is None or coverage.get("partial_evidence") is not False
            else "captured_declarations"
        ),
    }


def advice(app: Any, graph) -> dict:
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
        "basis": "Matching CI, component-interface and policy inputs; no checks were executed or reused.",
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
        }
    paths, comparison = _changed(app, graph, base_ref, base_snapshot_id, changed_paths)
    result = select_checks(
        declared, paths, graph.snapshot.files, cadence=cadence, runtime_context=runtime_context
    )
    app._require_live(graph)
    return result | {
        "comparison": comparison,
        "changed_paths": paths,
        "candidate_snapshot_id": graph.snapshot.id,
        "candidate_commit": graph.snapshot.revision,
        "policy_fingerprint": graph.snapshot.files[POLICY_PATH],
        "checks_executed": False,
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
