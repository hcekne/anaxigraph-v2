"""Plan declared CI tasks for a change in an application's checkout.

This was the product's `ci_plan` until November 2026. In nine pilot chains it answered
`not_configured` 147 of 147 times, because nobody authors the policy file it needs, and the
product now selects tests from recorded runs instead. The planner stays here for the drift
campaign and the hypotheses that qualified it.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from anaxigraph.application.delivery import (
    POLICY_PATH,
    _selection_validation,
    _validation_status,
    selection_evidence,
)
from anaxigraph.indexing import capture_sources

from .ci_policy import plan_ci as select_checks
from .ci_policy import validate_policy


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
    base_ref: str | None = None,
    base_snapshot_id: str | None = None,
    changed_paths: list[str] | None = None,
    cadence: str = "change",
    runtime_context: dict[str, Any] | None = None,
) -> dict:
    graph = app._live()
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
