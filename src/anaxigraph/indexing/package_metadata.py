"""Bounded declared root package facts, without importing or executing project code."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from .discovery import capture_sources

MAX_MANIFEST_BYTES = 65_536
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)(.*)$")
LIMITATIONS = [
    "Root declarations do not prove publication, installed versions or external implementation behavior.",
    "Only mapped root pyproject.toml and package.json are inspected; workspace members are not followed.",
]


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _names(value: Any) -> list[str]:
    entries = value if isinstance(value, (dict, list)) else []
    return [item[:200] for item in entries if isinstance(item, str)]


def _constraint(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return (
        "direct-reference-omitted"
        if any(marker in value for marker in ("://", "@", "git+"))
        else value[:240]
    )


def _dependencies(groups: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for kind, value in groups:
        entries = list(value.items()) if isinstance(value, dict) else []
        if isinstance(value, list):
            entries = [
                (match[1], match[2].strip())
                for item in value
                if isinstance(item, str) and (match := _NAME.match(item))
            ]
        result.extend(
            {"name": name[:160], "constraint": _constraint(constraint), "kind": kind}
            for name, constraint in entries
            if isinstance(name, str)
        )
    return result


def _facts(data: dict, python: bool) -> dict[str, Any]:
    project = _mapping(data.get("project")) if python else data
    tool = _mapping(data.get("tool"))
    if python:
        dependencies = [("runtime", project.get("dependencies", []))]
        dependencies.extend(
            (f"optional:{name}", values)
            for name, values in _mapping(project.get("optional-dependencies")).items()
        )
        members = _mapping(_mapping(tool.get("uv")).get("workspace")).get("members", [])
        tests = _mapping(_mapping(tool.get("pytest")).get("ini_options")).get("testpaths", [])
    else:
        dependencies = [
            (kind, data.get(kind, {}))
            for kind in (
                "dependencies",
                "devDependencies",
                "peerDependencies",
                "optionalDependencies",
            )
        ]
        workspace = data.get("workspaces", [])
        members = workspace.get("packages", []) if isinstance(workspace, dict) else workspace
        tests = []
    constraints, workspace, scripts = (
        _dependencies(dependencies),
        _names(members),
        _names(project.get("scripts")),
    )
    name, version = project.get("name"), project.get("version")
    return {
        "name": name[:160] if isinstance(name, str) else None,
        "version": version[:80] if isinstance(version, str) else None,
        "dependency_constraints": constraints[:40],
        "dependencies_truncated": len(constraints) > 40,
        "workspace_members": workspace[:20],
        "workspace_members_truncated": len(workspace) > 20,
        "script_names": scripts[:20],
        "script_names_truncated": len(scripts) > 20,
        "script_kind": "console-entry-point" if python else "package-script",
        "test_paths": _names(tests)[:20],
    }


def capture_package_metadata(root: Path, fingerprints: dict[str, str]) -> dict[str, Any]:
    manifests = []
    for path in ("pyproject.toml", "package.json"):
        if path not in fingerprints:
            continue
        item = {
            "path": path,
            "fingerprint": fingerprints[path],
            "status": "unknown",
            "evidence": [{"path": path, "line": 1}],
        }
        if (root / path).lstat().st_size > MAX_MANIFEST_BYTES:
            manifests.append(item | {"reason": "Manifest exceeds the 64 KiB metadata budget."})
            continue
        source = capture_sources(root, [path], fingerprints, maximum=MAX_MANIFEST_BYTES)[path]
        try:
            data = tomllib.loads(source) if path == "pyproject.toml" else json.loads(source)
        except (ValueError, TypeError):
            manifests.append(item | {"reason": "Manifest could not be parsed as package metadata."})
            continue
        if not isinstance(data, dict):
            manifests.append(item | {"reason": "Manifest root is not an object."})
            continue
        manifests.append(item | {"status": "parsed"} | _facts(data, path == "pyproject.toml"))
    parsed = sum(item["status"] == "parsed" for item in manifests)
    return {
        "status": "declared"
        if parsed == len(manifests) and parsed
        else "partial"
        if parsed
        else "unknown",
        "manifests": manifests,
        "limitations": LIMITATIONS,
    }
