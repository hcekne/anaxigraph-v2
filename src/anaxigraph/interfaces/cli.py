"""The uv-friendly command line; application decisions live in Application."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _location(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", type=Path, default=argparse.SUPPRESS, help="Checkout to analyze")
    parser.add_argument(
        "--database", type=Path, default=argparse.SUPPRESS, help="Local SQLite path"
    )
    parser.add_argument(
        "--exclusions",
        type=Path,
        default=argparse.SUPPRESS,
        help="JSON array of repository-relative exclusion patterns",
    )


def _ci_plan_parser(commands: Any) -> None:
    command = commands.add_parser(
        "ci-plan", help="Preview affected checks from declared task inputs and an explicit change"
    )
    _location(command)
    command.add_argument("--base-ref", help="Git revision to compare with the current checkout")
    command.add_argument("--base-snapshot-id", help="Retained snapshot to compare")
    command.add_argument(
        "--changed-paths", type=Path, help="JSON array of caller-supplied changed paths"
    )
    command.add_argument("--cadence", choices=("change", "release", "scheduled"), default="change")
    command.add_argument(
        "--runtime-context", type=Path, help="JSON object of declared runtime inputs"
    )


def parser() -> argparse.ArgumentParser:
    from anaxigraph import __version__

    result = argparse.ArgumentParser(description="AnaxiGraph V2 — understand and improve code")
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _location(result)
    commands = result.add_subparsers(dest="command", required=True)
    _ci_plan_parser(commands)
    for name, help_text in (
        ("scan", "Build or refresh the structural map"),
        ("status", "Show checkout identity and analysis readiness"),
        ("modules", "List mapped modules"),
        ("inspect", "Expand one entity and its relationships"),
        ("prepare", "Find ownership, reuse and checks for a change"),
        ("assess", "Assess proposed file placement"),
        ("validate", "Compare snapshots and attribute supplied check results"),
        ("request", "Prepare mapping, pattern, architecture, CI/CD or blind Fresh Eyes work"),
        ("submit", "Validate and save an agent's analysis result"),
        ("reviews", "Read saved architectural reviews"),
        ("run", "Run mapping, Patterns, component architecture, CI/CD or Fresh Eyes"),
        ("runs", "Show durable batch progress and failed batches"),
        ("resume", "Retry unfinished workflow batches; keep completed results"),
        ("workflow-config", "Read or save model choices and worker settings"),
        ("export", "Write the current portable record into anaxigraph/"),
        ("import", "Reuse compatible analyses from anaxigraph/"),
        ("evidence", "Read or export local task and model-usage evidence"),
        ("serve", "Open the local dashboard API"),
        ("mcp", "Serve the shared application over MCP stdio"),
    ):
        command = commands.add_parser(name, help=help_text)
        _location(command)
        if name == "scan":
            command.add_argument(
                "--export", action="store_true", help="Also publish the portable record"
            )
        elif name == "inspect":
            command.add_argument("entity_id")
            command.add_argument("--limit", type=int, default=30)
        elif name == "modules":
            command.add_argument("--sort", default="path")
            command.add_argument("--descending", action="store_true")
            command.add_argument("--limit", type=int, default=50)
            command.add_argument("--offset", type=int, default=0)
        elif name in {"prepare", "assess"}:
            command.add_argument("task")
            if name == "assess":
                command.add_argument("--files", nargs="+", required=True)
                command.add_argument(
                    "--decisions", type=Path, help="JSON array of proposal decisions"
                )
            else:
                command.add_argument("--limit", type=int, default=12)
                command.add_argument("--baseline-id", help="Explicitly pin a new change baseline")
                command.add_argument(
                    "--continue-task",
                    action="store_true",
                    help="Continue the existing task across new wording or commits",
                )
        elif name == "validate":
            command.add_argument("--receipts", type=Path, help="JSON array of check receipts")
            command.add_argument("--baseline-id", help="Compare against this retained snapshot")
        elif name == "request":
            command.add_argument(
                "kind",
                choices=(
                    "annotate",
                    "pattern-screen",
                    "patterns",
                    "architecture",
                    "delivery",
                    "fresh-eyes",
                ),
            )
            command.add_argument("--scope", nargs="+")
            command.add_argument("--pattern-ids", nargs="*", help="Explicit detailed-review cards")
            command.add_argument("--lens", choices=("boundaries", "packages", "verification"))
        elif name == "submit":
            command.add_argument("request_id")
            command.add_argument(
                "result", type=Path, help="JSON object containing the agent result"
            )
        elif name == "reviews":
            command.add_argument("--kind")
            command.add_argument("--limit", type=int, default=12)
            command.add_argument("--offset", type=int, default=0)
        elif name == "run":
            command.add_argument(
                "kind",
                choices=("mapping", "patterns", "architecture", "ci", "delivery", "fresh-eyes"),
            )
            command.add_argument("--scope", nargs="+")
            command.add_argument("--config", type=Path, help="Workflow settings JSON")
        elif name == "runs":
            command.add_argument("--run-id")
            command.add_argument("--batch-offset", type=int, default=0)
            command.add_argument("--batch-limit", type=int, default=10)
        elif name == "resume":
            command.add_argument("run_id")
        elif name == "workflow-config":
            command.add_argument(
                "--file", type=Path, help="Save model and parallelism settings JSON"
            )
        elif name == "evidence":
            command.add_argument("--export", action="store_true")
            command.add_argument("--limit", type=int, default=50)
            command.add_argument("--offset", type=int, default=0)
        elif name == "serve":
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=8766)
    return result


def _read_json(path: Path, expected: type) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, expected):
        raise ValueError(f"{path} must contain a JSON {expected.__name__}")
    return value


def _analysis_options(args: argparse.Namespace) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if args.pattern_ids is not None:
        options["pattern_ids"] = args.pattern_ids
    if args.lens is not None:
        options["lens"] = args.lens
    return options


def _check_dispatch(application: Any, args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "ci-plan":
        return application.ci_plan(
            base_ref=args.base_ref,
            base_snapshot_id=args.base_snapshot_id,
            changed_paths=_read_json(args.changed_paths, list) if args.changed_paths else None,
            cadence=args.cadence,
            runtime_context=_read_json(args.runtime_context, dict)
            if args.runtime_context
            else None,
        )
    receipts = _read_json(args.receipts, list) if args.receipts else None
    if args.baseline_id:
        return application.validate_change(receipts, args.baseline_id)
    return application.validate_change(receipts)


def dispatch(application: Any, args: argparse.Namespace) -> dict[str, Any]:
    """Keep transport dispatch independent from application implementation."""
    if args.command == "scan":
        return application.scan(publish=True) if args.export else application.scan()
    if args.command == "status":
        return application.status()
    if args.command == "modules":
        return application.modules(args.sort, args.descending, args.limit, args.offset)
    if args.command == "inspect":
        return application.inspect(args.entity_id, args.limit)
    if args.command == "prepare":
        if args.continue_task:
            return application.prepare_change(args.task, args.limit, args.baseline_id, True)
        if args.baseline_id:
            return application.prepare_change(args.task, args.limit, args.baseline_id)
        return application.prepare_change(args.task, args.limit)
    if args.command == "assess":
        if args.decisions is not None:
            return application.assess_plan(args.task, args.files, _read_json(args.decisions, list))
        return application.assess_plan(args.task, args.files)
    if args.command in {"validate", "ci-plan"}:
        return _check_dispatch(application, args)
    if args.command == "request":
        return application.analysis_request(args.kind, args.scope, **_analysis_options(args))
    if args.command == "submit":
        return application.submit_analysis(args.request_id, _read_json(args.result, dict))
    if args.command == "reviews":
        if args.limit != 12 or args.offset:
            return application.reviews(args.kind, args.limit, args.offset)
        return application.reviews(args.kind)
    if args.command == "run":
        config = _read_json(args.config, dict) if args.config else None
        return application.workflow_start(args.kind, config, args.scope, True)
    if args.command == "runs":
        return application.workflow_status(args.run_id, args.batch_offset, args.batch_limit)
    if args.command == "resume":
        return application.workflow_resume(args.run_id, True)
    if args.command == "workflow-config":
        config = _read_json(args.file, dict) if args.file else None
        return application.workflow_configure(config)
    if args.command == "evidence":
        return (
            application.export_evidence()
            if args.export
            else application.trial_evidence(args.limit, args.offset)
        )
    if args.command in {"export", "import"}:
        return getattr(application, f"{args.command}_record")()
    raise ValueError(f"Unsupported application command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    from anaxigraph.application import Application

    args = parser().parse_args(argv)
    try:
        exclusions = _read_json(args.exclusions, list) if hasattr(args, "exclusions") else []
        application = Application(
            getattr(args, "repo", Path.cwd()),
            getattr(args, "database", None),
            excluded=tuple(exclusions),
        )
        if args.command == "serve":
            import uvicorn

            from anaxigraph.interfaces.http import create_app

            uvicorn.run(create_app(application), host=args.host, port=args.port)
        elif args.command == "mcp":
            from anaxigraph.interfaces.mcp import create_mcp

            create_mcp(application).run(transport="stdio")
        else:
            print(json.dumps(dispatch(application, args), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
