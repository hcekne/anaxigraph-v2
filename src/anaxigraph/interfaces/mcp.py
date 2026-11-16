"""MCP uses exactly the same application answers as the dashboard and CLI."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import ConfigDict, Field, with_config
from typing_extensions import NotRequired, TypedDict

PageSize = Annotated[int, Field(ge=1, le=100)]
PageOffset = Annotated[int, Field(ge=0)]
BatchSize = Annotated[int, Field(ge=1, le=50)]
BatchOffset = Annotated[int, Field(ge=0, le=100_000)]
EvidenceSize = Annotated[int, Field(ge=1, le=1000)]


@with_config(ConfigDict(extra="forbid"))
class CheckReceipt(TypedDict):
    command: Annotated[str, Field(min_length=1, pattern=r"\S")]
    exit_code: Annotated[int, Field(strict=True)]
    snapshot_id: Annotated[str, Field(min_length=1, pattern=r"\S")]


@with_config(ConfigDict(extra="forbid"))
class PlanDecision(TypedDict):
    proposal_id: Annotated[str, Field(min_length=1, pattern=r"\S")]
    decision: Literal["adopt", "defer", "reject"]
    reason: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
    revisit_when: NotRequired[Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]]


def create_mcp(application: Any, *, operations: dict | None = None) -> FastMCP:
    server = FastMCP(
        "AnaxiGraph",
        stateless_http=True,
        json_response=True,
        instructions=(
            "Prepare, inspect, assess, validate; each call reads the checkout as it is, so no scan "
            "is needed first. completion_allowed=False means unqualified work. "
            "Resolve assess_plan decision_required or scope_incomplete before treating a plan as assessed; "
            "adopt, defer or reject each material proposal with a reason. Dispositions do not prove implementation. "
            "Run complete model workflows using workflow_start; inspect progress with workflow_status. "
            "Pattern workflows include boundary, standalone-package and independent-verification scouts. "
            "They also review CI/CD configuration. Use ci for pipeline review without semantic mapping. "
            "tests_for_change names the test files a recorded run says this checkout needs, and "
            "the whole suite when it cannot tell. "
            "Use architecture for those searches alone; assess_plan records explicit proposal decisions. "
            "Explicit request/submit packets remain available for independent agents. "
            "Fresh Eyes packets are implementation-blind; do not add source context to that task."
        ),
    )

    def tool(function, *, agents: bool = True):
        """Every operation serves the dashboard and CLI; an agent is offered only those it
        uses while coding, because each tool it is offered costs it context in every session.
        """
        if operations is not None:
            operations[function.__name__] = function
        return server.tool()(function) if agents else function

    def action(name, description, agents):
        def invoke() -> dict:
            return getattr(application, name)()

        invoke.__name__, invoke.__doc__ = name, description
        tool(invoke, agents=agents)

    for name, description, agents in (
        ("status", "Show checkout identity and independent map/annotation readiness.", True),
        ("scan", "Refresh source facts and report map health; analyzed code is never run.", True),
        (
            "export_record",
            "Save portable analyses in anaxigraph/; commit and push separately.",
            True,
        ),
        ("import_record", "Reuse Git-carried analyses matching this checkout.", False),
        ("portable_status", "Distinguish local map, portable record and commit/push state.", False),
        ("export_evidence", "Export bounded trial evidence without source or transcripts.", False),
    ):
        action(name, description, agents)

    @tool
    def modules(
        sort: str = "path", descending: bool = False, limit: PageSize = 50, offset: PageOffset = 0
    ) -> dict:
        """Discover modules in bounded pages, sortable by path, lines or function count."""
        return application.modules(sort, descending, limit, offset)

    @tool
    def inspect(
        entity_id: str, limit: PageSize = 30, offset: PageOffset = 0, full: bool = False
    ) -> dict:
        """Page observed relationships; full=True expands entity metadata and annotations."""
        return application.inspect(entity_id, limit, offset, full)

    @tool
    def prepare_change(
        task: str, limit: int = 12, baseline_id: str | None = None, continue_task: bool = False
    ) -> dict:
        """Find existing responsibilities, reuse opportunities and checks before editing."""
        return application.prepare_change(task, limit, baseline_id, continue_task)

    @tool
    def assess_plan(
        task: str,
        files: list[str],
        decisions: list[PlanDecision] | None = None,
        include_brief: bool = False,
    ) -> dict:
        """Return placement deltas and proposal decisions; include_brief=True repeats preparation."""
        return application.assess_plan(task, files, decisions, include_brief)

    @tool
    def validate_change(
        receipts: list[CheckReceipt] | None = None,
        baseline_id: str | None = None,
        runtime_context: dict[str, Any] | None = None,
        require_ci_evidence: bool = False,
    ) -> dict:
        """Validate exact inputs; completion_allowed=False requires resolving the evidence gaps."""
        return application.validate_change(
            receipts,
            baseline_id,
            runtime_context=runtime_context,
            require_ci_evidence=require_ci_evidence,
        )

    @tool
    def tests_for_change() -> dict:
        """Test files this checkout needs, from a recorded run of what each test executed and read."""
        return application.tests_for_change()

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
    def reviews(kind: str | None = None, limit: PageSize = 12, offset: PageOffset = 0) -> dict:
        """Discover saved review metadata; use inspect('review:<id>') for the full result."""
        return application.reviews(kind, limit, offset)

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
        run_id: str | None = None, batch_offset: BatchOffset = 0, batch_limit: BatchSize = 10
    ) -> dict:
        """Show progress; select a run and page its measured batch timings with batch_offset."""
        return application.workflow_status(run_id, batch_offset, batch_limit)

    @tool
    def workflow_resume(run_id: str) -> dict:
        """Retry failed or interrupted batches while retaining every completed batch."""
        return application.workflow_resume(run_id)

    def workflow_configure(config: dict[str, Any] | None = None) -> dict:
        """Read or save model choices by stage, stronger fallbacks and ordered review passes."""
        return application.workflow_configure(config)

    def trial_evidence(limit: EvidenceSize = 50, offset: PageOffset = 0) -> dict:
        """Read local task/context/usage evidence; unknown costs remain unknown."""
        return application.trial_evidence(limit, offset)

    tool(workflow_configure, agents=False)
    tool(trial_evidence, agents=False)
    return server
