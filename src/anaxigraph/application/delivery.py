"""CI-design context and review advice; project checks remain supplied reports."""

from __future__ import annotations

from typing import Any

from anaxigraph.analysis.ci_context import component_context
from anaxigraph.analysis.portable import input_identity
from anaxigraph.application.architecture import package_context
from anaxigraph.indexing.ci_evidence import capture_ci_evidence


def validation(checks: list[dict], *, checkout_current: bool = True) -> dict:
    """Attribute captured checks; an intervening checkout change leaves completion unqualified."""
    if any(check["status"] == "failed" for check in checks):
        status = "blocked"
    elif not checkout_current:
        status = "incomplete"
    elif not checks:
        status = "unchecked"
    else:
        status = (
            "reported-passed"
            if all(check["status"] == "passed" for check in checks)
            else "incomplete"
        )
    return {
        "validation_status": status,
        "behavioral_status": {"blocked": "failed", "incomplete": "unresolved"}.get(status, status),
        "completion_allowed": status == "reported-passed",
        "checkout_current": checkout_current,
    }


def context(app: Any, graph, pipeline_paths: list[str] | None = None) -> dict:
    selected = tuple(sorted(set(pipeline_paths or [])))
    cached = getattr(app, "_delivery_context_cache", None)
    if cached is None or cached[0] is not graph or cached[1] != selected:
        captured = capture_ci_evidence(app.root, graph.snapshot.files, list(selected) or None)
        captured = captured | {
            "components": component_context(graph),
            "package_context": package_context(app, graph),
            "test_selection": {
                "basis": "recorded-execution-and-reads",
                "next_step": "Use tests_for_change for current selection and its limitations.",
            },
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
