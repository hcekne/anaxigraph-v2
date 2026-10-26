"""MCP uses exactly the same application answers as the dashboard and CLI."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP


def create_mcp(application: Any) -> FastMCP:
    server = FastMCP(
        "AnaxiGraph V2",
        stateless_http=True,
        json_response=True,
        instructions=(
            "Prepare substantial changes, inspect evidence, assess placement, then scan and validate. "
            "Run complete model workflows using workflow_start; inspect progress with workflow_status. "
            "Explicit request/submit packets remain available for independent agents. "
            "Fresh Eyes packets are implementation-blind; do not add source context to that task."
        ),
    )

    @server.tool()
    def status() -> dict:
        """Show the exact checkout and independent map/annotation readiness."""
        return application.status()

    @server.tool()
    def scan() -> dict:
        """Refresh source facts without executing the analyzed code."""
        return application.scan()

    @server.tool()
    def modules(
        sort: str = "path", descending: bool = False, limit: int = 50, offset: int = 0
    ) -> dict:
        """Discover modules in bounded pages, sortable by path, lines or function count."""
        return application.modules(sort, descending, limit, offset)

    @server.tool()
    def inspect(entity_id: str, limit: int = 30) -> dict:
        """Expand a bounded entity neighborhood with evidence and resolution status."""
        return application.inspect(entity_id, limit)

    @server.tool()
    def prepare_change(
        task: str, limit: int = 12, baseline_id: str | None = None, continue_task: bool = False
    ) -> dict:
        """Find existing responsibilities, reuse opportunities and checks before editing."""
        if continue_task:
            return application.prepare_change(task, limit, baseline_id, True)
        if baseline_id:
            return application.prepare_change(task, limit, baseline_id)
        return application.prepare_change(task, limit)

    @server.tool()
    def assess_plan(task: str, files: list[str]) -> dict:
        """Check proposed placement against the shared graph and existing capabilities."""
        return application.assess_plan(task, files)

    @server.tool()
    def validate_change(
        receipts: list[dict[str, Any]] | None = None, baseline_id: str | None = None
    ) -> dict:
        """Compare saved states; supplied checks only count for their exact fingerprint."""
        if baseline_id:
            return application.validate_change(receipts, baseline_id)
        return application.validate_change(receipts)

    @server.tool()
    def analysis_request(
        kind: Literal["annotate", "patterns", "fresh-eyes"], scope: list[str] | None = None
    ) -> dict:
        """Prepare an annotation, catalogue-pattern or blind Fresh Eyes worker packet."""
        return application.analysis_request(kind, scope)

    @server.tool()
    def submit_analysis(request_id: str, result: dict[str, Any]) -> dict:
        """Validate and save a result for the exact requested state and analysis contract."""
        return application.submit_analysis(request_id, result)

    @server.tool()
    def reviews(kind: str | None = None, limit: int = 12, offset: int = 0) -> dict:
        """Discover saved review metadata; use inspect('review:<id>') for the full result."""
        if limit != 12 or offset:
            return application.reviews(kind, limit, offset)
        return application.reviews(kind)

    @server.tool()
    def workflow_start(
        kind: Literal["mapping", "annotate", "patterns", "fresh-eyes"],
        config: dict[str, Any] | None = None,
        scope: list[str] | None = None,
    ) -> dict:
        """Run a complete resumable mapping, Patterns or Fresh Eyes workflow in the background."""
        return application.workflow_start(kind, config, scope)

    @server.tool()
    def workflow_status(run_id: str | None = None) -> dict:
        """Show completed/total batches, active workers, elapsed time and final reconciliation."""
        return application.workflow_status(run_id)

    @server.tool()
    def workflow_resume(run_id: str) -> dict:
        """Retry failed or interrupted batches while retaining every completed batch."""
        return application.workflow_resume(run_id)

    @server.tool()
    def workflow_configure(config: dict[str, Any] | None = None) -> dict:
        """Read or save model choices by stage, stronger fallbacks and ordered review passes."""
        return application.workflow_configure(config)

    @server.tool()
    def export_record() -> dict:
        """Save current portable analyses in anaxigraph/; committing and pushing is separate."""
        return application.export_record()

    @server.tool()
    def import_record() -> dict:
        """Reuse Git-carried analyses whose content and context match this checkout."""
        return application.import_record()

    @server.tool()
    def portable_status() -> dict:
        """Distinguish local map, exported portable record and commit/push state."""
        return application.portable_status()

    @server.tool()
    def trial_evidence(limit: int = 50, offset: int = 0) -> dict:
        """Read local task/context/usage evidence; unknown costs remain unknown."""
        return application.trial_evidence(limit, offset)

    @server.tool()
    def export_evidence() -> dict:
        """Export bounded local trial evidence without source or model transcripts."""
        return application.export_evidence()

    return server
