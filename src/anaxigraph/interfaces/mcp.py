"""MCP uses exactly the same application answers as the dashboard and CLI."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP


def create_mcp(application: Any, *, operations: dict | None = None) -> FastMCP:
    server = FastMCP(
        "AnaxiGraph V2",
        stateless_http=True,
        json_response=True,
        instructions=(
            "Prepare substantial changes, inspect evidence, assess placement, then scan and validate. "
            "Resolve assess_plan decision_required or scope_incomplete before treating a plan as assessed; "
            "adopt, defer or reject each material proposal with a reason. Dispositions do not prove implementation. "
            "Run complete model workflows using workflow_start; inspect progress with workflow_status. "
            "Pattern workflows include boundary, standalone-package and independent-verification scouts. "
            "They also review CI/CD configuration. Use ci for pipeline review without semantic mapping; "
            "ci_plan selects declared checks for an explicit change, not from heuristic impact links. "
            "Use architecture for those searches alone; assess_plan records explicit proposal decisions. "
            "Explicit request/submit packets remain available for independent agents. "
            "Fresh Eyes packets are implementation-blind; do not add source context to that task."
        ),
    )

    def tool(function):
        if operations is not None:
            operations[function.__name__] = function
        return server.tool()(function)

    @tool
    def status() -> dict:
        """Show the exact checkout and independent map/annotation readiness."""
        return application.status()

    @tool
    def scan() -> dict:
        """Refresh source facts without executing the analyzed code."""
        return application.scan()

    @tool
    def modules(
        sort: str = "path", descending: bool = False, limit: int = 50, offset: int = 0
    ) -> dict:
        """Discover modules in bounded pages, sortable by path, lines or function count."""
        return application.modules(sort, descending, limit, offset)

    @tool
    def inspect(entity_id: str, limit: int = 30) -> dict:
        """Expand a bounded entity neighborhood with evidence and resolution status."""
        return application.inspect(entity_id, limit)

    @tool
    def prepare_change(
        task: str, limit: int = 12, baseline_id: str | None = None, continue_task: bool = False
    ) -> dict:
        """Find existing responsibilities, reuse opportunities and checks before editing."""
        if continue_task:
            return application.prepare_change(task, limit, baseline_id, True)
        if baseline_id:
            return application.prepare_change(task, limit, baseline_id)
        return application.prepare_change(task, limit)

    @tool
    def assess_plan(
        task: str, files: list[str], decisions: list[dict[str, Any]] | None = None
    ) -> dict:
        """Check placement and explicitly adopt, defer or reject disclosed architecture proposals."""
        if decisions is not None:
            return application.assess_plan(task, files, decisions)
        return application.assess_plan(task, files)

    @tool
    def validate_change(
        receipts: list[dict[str, Any]] | None = None, baseline_id: str | None = None
    ) -> dict:
        """Compare saved states; supplied checks only count for their exact fingerprint."""
        if baseline_id:
            return application.validate_change(receipts, baseline_id)
        return application.validate_change(receipts)

    @tool
    def ci_plan(
        base_ref: str | None = None,
        base_snapshot_id: str | None = None,
        changed_paths: list[str] | None = None,
        cadence: Literal["change", "release", "scheduled"] = "change",
        runtime_context: dict[str, Any] | None = None,
    ) -> dict:
        """Explain selected checks from declared task inputs, ordering and data dependencies."""
        return application.ci_plan(
            base_ref=base_ref,
            base_snapshot_id=base_snapshot_id,
            changed_paths=changed_paths,
            cadence=cadence,
            runtime_context=runtime_context,
        )

    @tool
    def analysis_request(
        kind: Literal[
            "annotate", "pattern-screen", "patterns", "architecture", "delivery", "fresh-eyes"
        ],
        scope: list[str] | None = None,
        pattern_ids: list[str] | None = None,
        lens: Literal["boundaries", "packages", "verification"] | None = None,
    ) -> dict:
        """Prepare mapping, pattern, architecture, CI/CD or blind Fresh Eyes work."""
        options: dict[str, Any] = {}
        if pattern_ids is not None:
            options["pattern_ids"] = pattern_ids
        if lens is not None:
            options["lens"] = lens
        return application.analysis_request(kind, scope, **options)

    @tool
    def submit_analysis(request_id: str, result: dict[str, Any]) -> dict:
        """Validate and save a result for the exact requested state and analysis contract."""
        return application.submit_analysis(request_id, result)

    @tool
    def reviews(kind: str | None = None, limit: int = 12, offset: int = 0) -> dict:
        """Discover saved review metadata; use inspect('review:<id>') for the full result."""
        if limit != 12 or offset:
            return application.reviews(kind, limit, offset)
        return application.reviews(kind)

    @tool
    def workflow_start(
        kind: Literal[
            "mapping", "annotate", "patterns", "architecture", "ci", "delivery", "fresh-eyes"
        ],
        config: dict[str, Any] | None = None,
        scope: list[str] | None = None,
    ) -> dict:
        """Run resumable mapping, Patterns, component architecture, CI/CD, or blind Fresh Eyes."""
        return application.workflow_start(kind, config, scope)

    @tool
    def workflow_status(
        run_id: str | None = None, batch_offset: int = 0, batch_limit: int = 10
    ) -> dict:
        """Show progress; select a run and page its measured batch timings with batch_offset."""
        return application.workflow_status(run_id, batch_offset, batch_limit)

    @tool
    def workflow_resume(run_id: str) -> dict:
        """Retry failed or interrupted batches while retaining every completed batch."""
        return application.workflow_resume(run_id)

    @tool
    def workflow_configure(config: dict[str, Any] | None = None) -> dict:
        """Read or save model choices by stage, stronger fallbacks and ordered review passes."""
        return application.workflow_configure(config)

    @tool
    def export_record() -> dict:
        """Save current portable analyses in anaxigraph/; committing and pushing is separate."""
        return application.export_record()

    @tool
    def import_record() -> dict:
        """Reuse Git-carried analyses whose content and context match this checkout."""
        return application.import_record()

    @tool
    def portable_status() -> dict:
        """Distinguish local map, exported portable record and commit/push state."""
        return application.portable_status()

    @tool
    def trial_evidence(limit: int = 50, offset: int = 0) -> dict:
        """Read local task/context/usage evidence; unknown costs remain unknown."""
        return application.trial_evidence(limit, offset)

    @tool
    def export_evidence() -> dict:
        """Export bounded local trial evidence without source or model transcripts."""
        return application.export_evidence()

    return server
