"""Bounded CI declarations, without evaluating YAML expressions or running project code."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .discovery import capture_sources

MAX_FILES, MAX_LOCAL_FILES, MAX_BYTES, MAX_JOBS, MAX_STEPS = 16, 8, 65_536, 30, 20
PROJECT_ID = "project"
_SENSITIVE = re.compile(
    r"(?i)secret|password|authorization|api[_-]?key|credential|\btoken\b|\blogin\b"
)
_URL = re.compile(r"(?:https?|ssh|git\+https?)://[^\s'\"<>]+", re.I)
_TOKEN = re.compile(r"\b(?:sk-|ghp_|github_pat_|xox[bap]-|AKIA)[A-Za-z0-9_\-]+")
_ASSIGNMENT = re.compile(r"\b([A-Za-z_][A-Za-z_0-9]*)=(?:\"[^\"]*\"|'[^']*'|[^\s;]+)")
_SCRIPT_PATH = re.compile(r"(?<![\w./-])(?:\./)?[A-Za-z0-9_./-]+\.(?:py|sh|bash|js|ts|mjs|cjs)\b")
_UNSUPPORTED = {
    ".gitlab-ci.yml",
    "Jenkinsfile",
    "azure-pipelines.yml",
    "bitbucket-pipelines.yml",
    ".circleci/config.yml",
}


def _workflow(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        len(parts) == 3
        and parts[:2] == (".github", "workflows")
        and parts[-1].endswith((".yml", ".yaml"))
    )


def _safe(value: str, limit: int = 300) -> str:
    if _SENSITIVE.search(value):
        return "[credential-related text omitted]"
    value = _ASSIGNMENT.sub(r"\1=[value omitted]", value)
    return _TOKEN.sub("[credential omitted]", _URL.sub("[URL omitted]", value))[:limit]


def redacted_command(text: str) -> str:
    """Bound command previews and omit credential markers, URLs and assignments."""
    return _safe(text)


def _guard(root: Node) -> bool:
    pending: list[tuple[Node, int, frozenset[int]]] = [(root, 0, frozenset())]
    visited, truncated = 0, False
    while pending:
        node, depth, ancestors = pending.pop()
        visited += 1
        if id(node) in ancestors or depth > 30 or visited > 10_000:
            raise ValueError("YAML nesting, alias cycle or node budget exceeded")
        children = (
            [part for pair in node.value for part in pair]
            if isinstance(node, MappingNode)
            else node.value
            if isinstance(node, SequenceNode)
            else []
        )
        truncated |= len(children) > 20 or (isinstance(node, ScalarNode) and len(node.value) > 300)
        pending.extend((child, depth + 1, ancestors | {id(node)}) for child in children)
    return truncated


def _mapping(node: Node | None) -> dict[str, Node]:
    if not isinstance(node, MappingNode):
        return {}
    result: dict[str, Node] = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.value in result:
            raise ValueError("YAML mapping keys must be distinct scalars")
        result[key.value] = value
    return result


def _scalar(node: Node | None, default: str = "") -> str:
    return _safe(node.value) if isinstance(node, ScalarNode) else default


def _value(node: Node | None) -> Any:
    if isinstance(node, ScalarNode):
        return _safe(node.value)
    if isinstance(node, SequenceNode):
        return [_value(child) for child in node.value[:20]]
    return {
        key: _value(value)
        for key, value in list(_mapping(node).items())[:20]
        if key not in {"env", "secrets"} and not _SENSITIVE.search(key)
    }


def _sequence(node: Node | None) -> list[Node]:
    return node.value if isinstance(node, SequenceNode) else []


def _working_directory(data: dict[str, Node], fallback: str = ".") -> str:
    run = _mapping(_mapping(data.get("defaults")).get("run"))
    return _scalar(run.get("working-directory"), fallback)


def _steps(node: Node | None, directory: str) -> list[dict]:
    result = []
    for child in _sequence(node)[:MAX_STEPS]:
        step = _mapping(child)
        fields = {
            key: _value(step[key])
            for key in ("uses", "run", "if", "continue-on-error")
            if key in step
        }
        allowed = {
            "key",
            "path",
            "restore-keys",
            "name",
            "cache",
            "cache-dependency-path",
            "python-version",
            "node-version",
        }
        fields["with"] = {
            key: _value(value)
            for key, value in _mapping(step.get("with")).items()
            if key in allowed
        }
        result.append(
            fields
            | {
                "line": child.start_mark.line + 1,
                "working_directory": _scalar(step.get("working-directory"), directory),
            }
        )
    return result


def _job(identity: str, node: Node, directory: str) -> dict:
    data = _mapping(node)
    directory = _working_directory(data, directory)
    needs = data.get("needs")
    dependencies = (
        [_scalar(child) for child in _sequence(needs)[:MAX_JOBS]]
        if isinstance(needs, SequenceNode)
        else [_scalar(needs)]
        if needs
        else []
    )
    fields = {
        key: _value(data[key])
        for key in (
            "if",
            "strategy",
            "runs-on",
            "timeout-minutes",
            "concurrency",
            "environment",
            "uses",
        )
        if key in data
    }
    steps = _sequence(data.get("steps"))
    return fields | {
        "id": identity,
        "line": node.start_mark.line + 1,
        "needs": dependencies,
        "needs_dynamic": any("${{" in item for item in dependencies),
        "needs_truncated": len(_sequence(needs)) > MAX_JOBS,
        "working_directory": directory,
        "steps": _steps(data.get("steps"), directory),
        "steps_total": len(steps),
        "steps_truncated": len(steps) > MAX_STEPS,
    }


def _parse(path: str, source: str) -> dict:
    node = yaml.compose(source, Loader=yaml.BaseLoader)
    if not isinstance(node, MappingNode):
        raise ValueError("CI configuration must be a YAML mapping")
    truncated = _guard(node)
    data = _mapping(node)
    if _workflow(path):
        jobs = _mapping(data.get("jobs"))
        return {
            "status": "parsed",
            "kind": "github-actions",
            "triggers": _value(data.get("on")),
            "values_truncated": truncated,
            "concurrency": _value(data.get("concurrency")),
            "jobs_total": len(jobs),
            "jobs_truncated": len(jobs) > MAX_JOBS,
            "jobs": [
                _job(key, value, _working_directory(data))
                for key, value in list(jobs.items())[:MAX_JOBS]
            ],
        }
    runs = _mapping(data.get("runs"))
    return {
        "status": "parsed",
        "kind": "local-action",
        "using": _scalar(runs.get("using")),
        "values_truncated": truncated,
        "steps": _steps(runs.get("steps"), "."),
        "steps_total": len(_sequence(runs.get("steps"))),
        "steps_truncated": len(_sequence(runs.get("steps"))) > MAX_STEPS,
    }


def _capture(root: Path, files: dict[str, str], path: str, sources: list[dict]) -> dict:
    item = {"id": path, "path": path, "fingerprint": files[path], "status": "unknown"}
    if (root / path).lstat().st_size > MAX_BYTES:
        return item | {"reason": "CI file exceeds its 64 KiB capture budget.", "line_count": None}
    source = capture_sources(root, [path], files, maximum=MAX_BYTES)[path]
    lines = max(1, len(source.splitlines()))
    sources.append({"path": path, "fingerprint": files[path], "line_count": lines})
    if path in _UNSUPPORTED:
        return item | {"status": "unsupported", "line_count": lines}
    try:
        projected = _parse(path, source)
        if len(json.dumps(projected).encode()) > MAX_BYTES:
            return item | {
                "line_count": lines,
                "values_truncated": True,
                "reason": "Projected CI metadata exceeds its 64 KiB budget.",
            }
        return item | {"line_count": lines} | projected
    except (yaml.YAMLError, ValueError, RecursionError):
        return item | {
            "line_count": lines,
            "reason": "CI configuration could not be projected within YAML safety/shape bounds.",
        }


def _uses(item: dict) -> list[str]:
    result = [job.get("uses", "") for job in item.get("jobs", [])]
    steps = item.get("steps", []) + [step for job in item.get("jobs", []) for step in job["steps"]]
    return result + [step.get("uses", "") for step in steps]


def _local_path(reference: str, files: dict[str, str]) -> str | None:
    if not reference.startswith("./"):
        return None
    relative = reference[2:].rstrip("/")
    if ".." in PurePosixPath(relative).parts:
        return None
    candidates = (
        (relative, relative + "/action.yml", relative + "/action.yaml")
        if relative
        else ("action.yml", "action.yaml")
    )
    return next(
        (path for path in candidates if path in files),
        None,
    )


def _related(
    root: Path, files: dict[str, str], pipelines: list[dict], sources: list[dict]
) -> list[dict]:
    seen = {item["id"] for item in pipelines}
    result: list[dict] = []
    pending = list(pipelines)
    while pending and len(result) < MAX_LOCAL_FILES:
        current = pending.pop(0)
        for reference in _uses(current):
            path = _local_path(reference, files)
            if path is not None and path not in seen:
                seen.add(path)
                captured = _capture(root, files, path, sources)
                result.append(captured)
                pending.append(captured)
                if len(result) == MAX_LOCAL_FILES:
                    break
    return result


def _scripts(root: Path, files: dict[str, str], sources: list[dict]) -> dict:
    path = "package.json"
    if path not in files or (root / path).lstat().st_size > MAX_BYTES:
        return {"status": "unknown", "scripts": []}
    source = capture_sources(root, [path], files, maximum=MAX_BYTES)[path]
    sources.append(
        {"path": path, "fingerprint": files[path], "line_count": max(1, len(source.splitlines()))}
    )
    try:
        data = json.loads(source)
    except ValueError:
        return {"status": "unknown", "scripts": []}
    scripts = data.get("scripts", {}) if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return {"status": "unknown", "scripts": []}
    return {
        "status": "declared",
        "truncated": len(scripts) > 40,
        "scripts": [
            {
                "name": _safe(name, 100),
                "command": _safe(value),
                "command_projected": _safe(value) != value,
                "working_directory": ".",
                "evidence": [{"path": path, "line": 1}],
            }
            for name, value in list(scripts.items())[:40]
            if isinstance(name, str) and isinstance(value, str)
        ],
    }


def _script_references(items: list[dict], scripts: dict, files: dict[str, str]) -> dict:
    commands = [script["command"] for script in scripts["scripts"]]
    for item in items:
        steps = item.get("steps", []) + [
            step for job in item.get("jobs", []) for step in job["steps"]
        ]
        commands.extend(step["run"] for step in steps if isinstance(step.get("run"), str))
    paths = {
        path.removeprefix("./") for command in commands for path in _SCRIPT_PATH.findall(command)
    }
    present = sorted(
        path for path in paths if ".." not in PurePosixPath(path).parts and path in files
    )
    return {
        "files": [
            {"path": path, "fingerprint": files[path], "status": "present-body-opaque"}
            for path in present[:40]
        ],
        "truncated": len(present) > 40,
        "scope": "Literal script paths in captured command previews only; dynamic references and script bodies remain unknown.",
    }


def capture_ci_evidence(
    root: Path, fingerprints: dict[str, str], pipeline_paths: list[str] | None = None
) -> dict:
    """Capture exact mapped declarations; expressions and remote actions stay opaque."""
    candidates = sorted(path for path in fingerprints if _workflow(path) or path in _UNSUPPORTED)
    if pipeline_paths is not None:
        if not isinstance(pipeline_paths, list) or any(
            not isinstance(path, str) for path in pipeline_paths
        ):
            raise ValueError("pipeline_paths must be a list of mapped CI paths")
        if len(set(pipeline_paths)) != len(pipeline_paths) or set(pipeline_paths) - set(candidates):
            raise ValueError("pipeline_paths contains duplicate or unknown CI paths")
        if pipeline_paths:
            candidates = pipeline_paths
    sources: list[dict] = []
    pipelines = [_capture(root, fingerprints, path, sources) for path in candidates[:MAX_FILES]]
    related = _related(root, fingerprints, pipelines, sources)
    scripts = _scripts(root, fingerprints, sources)
    return {
        "pipelines": pipelines or [{"id": PROJECT_ID, "status": "missing", "jobs": []}],
        "pipelines_total": len(candidates),
        "pipelines_truncated": len(candidates) > MAX_FILES,
        "local_references": related,
        "local_references_may_be_truncated": len(related) == MAX_LOCAL_FILES,
        "package_scripts": scripts,
        "script_references": _script_references(pipelines + related, scripts, fingerprints),
        "source_files": sources,
        "timings": {"status": "not_supplied"},
        "capture_limits": {
            "primary_files": MAX_FILES,
            "local_files": MAX_LOCAL_FILES,
            "file_bytes": MAX_BYTES,
            "jobs_per_file": MAX_JOBS,
            "steps_per_job": MAX_STEPS,
        },
        "limitations": [
            "GitHub declarations only; expressions and external action/reusable-workflow implementations were not evaluated.",
            "Bounded projections can omit jobs, steps, matrices and referenced files; flags and unknowns must remain visible.",
            "Environment/secret values are excluded and credential-related commands/URLs omitted; captured commands are never executed.",
            "No CI duration, critical-path or saved-time measurements were supplied.",
        ],
    }
