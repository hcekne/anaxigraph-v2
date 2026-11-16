"""The uv-friendly command line; application decisions live in Application."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _location(parser: argparse.ArgumentParser) -> None:
    for name, description in (
        ("repo", "Checkout to analyze"),
        ("database", "Local SQLite path"),
        ("exclusions", "JSON array of repository-relative exclusion patterns"),
    ):
        parser.add_argument(f"--{name}", type=Path, default=argparse.SUPPRESS, help=description)


def _test_parsers(commands: Any) -> None:
    command = commands.add_parser(
        "tests", help="Test files this checkout needs, per recorded suite"
    )
    _location(command)
    command.add_argument("--paths", action="store_true", help="Print only the test file paths")
    command.add_argument(
        "--recorder", action="store_true", help="Print the folder of the reads recorder plugin"
    )
    command.add_argument(
        "--ran", type=Path, nargs="+", help="JUnit reports of a whole-suite run at this checkout"
    )
    command = commands.add_parser("record-tests", help="Save a record of one suite's recorded run")
    _location(command)
    command.add_argument("--coverage", type=Path, required=True, help="coverage.py data file")
    command.add_argument("--suite", required=True, help="Name of the suite, such as backend")
    command.add_argument("--scope", nargs="+", required=True, help="Folders the suite depends on")
    command.add_argument("--run-root", default=".", help="Folder the suite ran in")
    command.add_argument("--recorded-root", help="Checkout path on the recording machine")
    command.add_argument("--junit", type=Path, nargs="+", help="JUnit reports of that run")
    command.add_argument("--reads", type=Path, help="Reads recorder report of the same run")


def _test_dispatch(application: Any, args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "record-tests":
        return application.record_tests(
            args.coverage,
            args.suite,
            args.scope,
            args.run_root,
            args.recorded_root,
            args.junit,
            args.reads,
        )
    result = application.tests_for_change(full=True, ran=args.ran)
    if args.paths:  # For a CI step: one path per line, or nothing when the answer is "all".
        selected = [suite for suite in result["suites"] if suite["status"] != "all"]
        if len(selected) == len(result["suites"]):
            return {"_lines": [path for suite in selected for path in suite["test_files"]]}
        return {"_lines": []}
    return result


def parser() -> argparse.ArgumentParser:
    from anaxigraph import __version__

    result = argparse.ArgumentParser(description="AnaxiGraph — understand and improve code")
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _location(result)
    commands = result.add_subparsers(dest="command", required=True)
    _test_parsers(commands)
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
            command.add_argument("--offset", type=int, default=0)
            command.add_argument("--full", action="store_true")
        elif name == "modules":
            command.add_argument("--sort", default="path")
            command.add_argument("--descending", action="store_true")
            command.add_argument("--limit", type=int, default=50)
            command.add_argument("--offset", type=int, default=0)
        elif name in {"prepare", "assess"}:
            command.add_argument("task")
            if name == "assess":
                command.add_argument("--files", nargs="+", required=True)
                command.add_argument("--include-brief", action="store_true")
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
            command.add_argument(
                "--allow-origin",
                action="append",
                default=[],
                help="Exact trusted server URL; LAN requires ANAXIGRAPH_HTTP_TOKEN",
            )
    return result


def _read_json(path: Path | None, expected: type) -> Any:
    if path is None:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, expected):
        raise ValueError(f"{path} must contain a JSON {expected.__name__}")
    return value


def dispatch(application: Any, args: argparse.Namespace) -> dict[str, Any]:
    """Keep transport dispatch independent from application implementation."""
    if args.command == "scan":
        return application.scan(publish=True) if args.export else application.scan()
    if args.command == "status":
        return application.status()
    if args.command == "modules":
        return application.modules(args.sort, args.descending, args.limit, args.offset)
    if args.command == "inspect":
        return application.inspect(args.entity_id, args.limit, args.offset, args.full)
    if args.command == "prepare":
        return application.prepare_change(
            args.task, args.limit, args.baseline_id, args.continue_task
        )
    if args.command == "assess":
        return application.assess_plan(
            args.task, args.files, _read_json(args.decisions, list), args.include_brief
        )
    if args.command == "validate":
        return application.validate_change(
            _read_json(args.receipts, list),
            args.baseline_id,
        )
    if args.command in {"tests", "record-tests"}:
        return _test_dispatch(application, args)
    if args.command == "request":
        options = {
            key: getattr(args, key)
            for key in ("pattern_ids", "lens")
            if getattr(args, key) is not None
        }
        return application.analysis_request(args.kind, args.scope, **options)
    if args.command == "submit":
        return application.submit_analysis(args.request_id, _read_json(args.result, dict))
    if args.command == "reviews":
        return application.reviews(args.kind, args.limit, args.offset)
    if args.command == "run":
        return application.workflow_start(
            args.kind, _read_json(args.config, dict), args.scope, True
        )
    if args.command == "runs":
        return application.workflow_status(args.run_id, args.batch_offset, args.batch_limit)
    if args.command == "resume":
        return application.workflow_resume(args.run_id, True)
    if args.command == "workflow-config":
        return application.workflow_configure(_read_json(args.file, dict))
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
    if getattr(args, "recorder", False):  # For PYTHONPATH in a project's test setup.
        print(Path(__file__).parents[1] / "recorder")  # Needs no checkout and leaves no state.
        return 0
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

            token = os.environ.get("ANAXIGRAPH_HTTP_TOKEN")
            if args.host not in {"127.0.0.1", "localhost", "::1"} and (
                not token or not args.allow_origin
            ):
                raise ValueError("LAN serving requires --allow-origin and ANAXIGRAPH_HTTP_TOKEN")
            uvicorn.run(
                create_app(application, allowed_origins=args.allow_origin, auth_token=token),
                host=args.host,
                port=args.port,
            )
        elif args.command == "mcp":
            from anaxigraph.interfaces.mcp import create_mcp

            create_mcp(application).run(transport="stdio")
        else:
            result = dispatch(application, args)
            lines = result.get("_lines")
            shown = json.dumps(result, ensure_ascii=False, indent=2)
            print("\n".join(lines) if lines is not None else shown)
            if args.command == "validate" and result.get("validation_status") == "blocked":
                return 0 if result.get("completion_allowed") else 1
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
