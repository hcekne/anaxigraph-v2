"""Select declared CI tasks without executing commands or inferring test coverage."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import re
from collections import defaultdict
from functools import lru_cache
from typing import Any

CADENCES = {"change", "release", "scheduled"}
FIELDS = {
    "id",
    "command",
    "working_directory",
    "inputs",
    "needs",
    "consumes",
    "cadence",
    "kind",
    "cache",
    "estimated_seconds",
}


def _strings(value: Any, label: str, maximum: int = 1000) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > maximum
        or any(not isinstance(x, str) or not x or len(x) > 4096 for x in value)
    ):
        raise ValueError(f"CI policy {label} must be a bounded list of nonempty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"CI policy {label} must not contain duplicates")
    return list(value)


def _path(value: str, *, pattern: bool = False) -> str:
    if (
        value.startswith("/")
        or "\\" in value
        or "\0" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("CI paths and globs must be repository-relative POSIX paths")
    if not pattern and any(char in value for char in "*?["):
        raise ValueError("CI changed paths and working directories cannot contain wildcards")
    return value


def _patterns(value: Any, label: str) -> list[str]:
    return [_path(item, pattern=True) for item in _strings(value, label, 100)]


def _closure(states: set[int], parts: tuple[str, ...]) -> set[int]:
    pending = list(states)
    while pending:
        index = pending.pop()
        if index < len(parts) and parts[index] == "**" and index + 1 not in states:
            states.add(index + 1)
            pending.append(index + 1)
    return states


@lru_cache(maxsize=131_072)
def _matches(pattern: str, path: str) -> bool:
    """POSIX segments: * stays in one segment, ** matches zero or more segments."""
    parts = tuple(pattern.split("/"))
    states = _closure({0}, parts)
    for segment in path.split("/"):
        following = set()
        for index in states:
            if index < len(parts) and parts[index] == "**":
                following.add(index)
            elif index < len(parts) and fnmatch.fnmatchcase(segment, parts[index]):
                following.add(index + 1)
        states = _closure(following, parts)
    return len(parts) in states


def _matches_any(patterns: list[str], path: str) -> bool:
    return any(_matches(pattern, path) for pattern in patterns)


def _working_directory(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("CI working_directory must be a relative directory")
    return value if value == "." else _path(value)


def _duration(value: Any) -> None:
    if value is not None and (
        type(value) not in {int, float} or not 0 <= value <= 1e12 or not math.isfinite(value)
    ):
        raise ValueError("CI duration must be null or a finite nonnegative estimate")


def _target(value: Any, always: list[str]) -> dict:
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError("CI target has unknown fields or is not an object")
    identity, command = value.get("id"), value.get("command")
    if not isinstance(identity, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", identity
    ):
        raise ValueError("CI target needs a short stable id")
    if not isinstance(command, str) or not 1 <= len(command.strip()) <= 2000:
        raise ValueError("CI target needs a nonempty command of at most 2,000 characters")
    target = {
        "id": identity,
        "command": command.strip(),
        "working_directory": _working_directory(value.get("working_directory", ".")),
        "inputs": _patterns(value.get("inputs", []), "inputs"),
        "needs": _strings(value.get("needs", []), "needs"),
        "consumes": _strings(value.get("consumes", []), "consumes"),
        "cadence": value.get("cadence", "change"),
        "kind": value.get("kind", "test"),
        "cache": value.get("cache", False),
        "estimated_seconds": value.get("estimated_seconds"),
    }
    if not target["inputs"] and identity not in always:
        raise ValueError("CI targets require inputs unless explicitly retained in always")
    if (
        target["cadence"] not in tuple(CADENCES)
        or target["kind"] not in ("test", "build", "deploy", "check")
        or type(target["cache"]) is not bool
    ):
        raise ValueError("CI target cadence, kind or cache setting is invalid")
    _duration(target["estimated_seconds"])
    return target


def _layers(targets: dict[str, dict], selected: set[str]) -> list[list[str]]:
    dependencies = {
        key: (set(targets[key]["needs"]) | set(targets[key]["consumes"])) & selected
        for key in selected
    }
    followers: dict[str, set[str]] = defaultdict(set)
    for key, values in dependencies.items():
        for parent in values:
            followers[parent].add(key)
    ready = sorted(key for key, values in dependencies.items() if not values)
    layers, visited = [], set()
    while ready:
        layers.append(ready)
        following = set()
        for key in ready:
            visited.add(key)
            for child in followers[key]:
                dependencies[child].remove(key)
                if not dependencies[child]:
                    following.add(child)
        ready = sorted(following)
    if visited != selected:
        raise ValueError("CI needs/consumes dependencies contain a cycle")
    return layers


def validate_policy(value: Any) -> dict:
    allowed = {"version", "targets", "always", "fallback", "shared_inputs", "ignored_inputs"}
    if (
        not isinstance(value, dict)
        or set(value) - allowed
        or type(value.get("version")) is not int
        or value["version"] != 1
    ):
        raise ValueError("CI policy must use version 1 and supported fields")
    if len(json.dumps(value, allow_nan=False).encode()) > 1_000_000:
        raise ValueError("CI policy exceeds its 1 MB budget")
    raw = value.get("targets")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 1000:
        raise ValueError("CI policy requires one to 1,000 targets")
    always, fallback = (
        _strings(value.get("always", []), "always"),
        _strings(value.get("fallback", []), "fallback"),
    )
    targets = [_target(item, always) for item in raw]
    by_id = {item["id"]: item for item in targets}
    if len(by_id) != len(targets):
        raise ValueError("CI target ids must be unique")
    references = set(always + fallback)
    for item in targets:
        references.update(item["needs"] + item["consumes"])
    if references - by_id.keys():
        raise ValueError("CI policy refers to unknown target ids")
    _layers(by_id, set(by_id))
    return {
        "version": 1,
        "targets": targets,
        "always": always,
        "fallback": fallback,
        "shared_inputs": _patterns(value.get("shared_inputs", []), "shared_inputs"),
        "ignored_inputs": _patterns(value.get("ignored_inputs", []), "ignored_inputs"),
    }


def _invalidation(
    targets: dict[str, dict], paths: list[str], shared: list[str]
) -> tuple[set[str], dict[str, list[str]]]:
    affected, reasons = set(), defaultdict(list)
    for key, target in targets.items():
        changed = [path for path in paths if _matches_any(target["inputs"], path)]
        if changed or shared:
            affected.add(key)
            reasons[key].extend([f"Changed input: {path}" for path in changed[:4]])
            reasons[key].extend([f"Shared input: {path}" for path in shared[:4]])
    pending = list(affected)
    while pending:
        parent = pending.pop()
        for key, target in targets.items():
            if parent in target["consumes"]:
                reasons[key].append(f"Consumes changed output of {parent}")
                if key not in affected:
                    affected.add(key)
                    pending.append(key)
    return affected, reasons


def _coverage(targets: dict[str, dict], eligible: set[str], layers: list[list[str]]) -> set[str]:
    reachable = set(eligible)
    for layer in reversed(layers):
        for key in layer:
            if key in reachable:
                reachable.update(targets[key]["consumes"])
    return reachable


def _prerequisites(
    targets: dict[str, dict], selected: set[str], reasons: dict[str, list[str]]
) -> None:
    pending = list(selected)
    while pending:
        child = pending.pop()
        for parent in set(targets[child]["needs"] + targets[child]["consumes"]):
            reasons[parent].append(f"Prerequisite of selected target {child}")
            if parent not in selected:
                selected.add(parent)
                pending.append(parent)


def _runtime(value: Any) -> dict | None:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or len(value) > 32
        or any(not isinstance(key, str) or not key or len(key) > 100 for key in value)
    ):
        raise ValueError("CI runtime_context must be a bounded flat object")
    for item in value.values():
        if item is not None and type(item) not in {str, int, float, bool}:
            raise ValueError("CI runtime fingerprints must be scalar values")
        if (
            isinstance(item, str)
            and len(item) > 1000
            or isinstance(item, float)
            and not math.isfinite(item)
        ):
            raise ValueError("CI runtime fingerprints must be bounded and finite")
    return value


def _cache(
    target: dict,
    policy: dict,
    files: dict[str, str],
    paths: list[str],
    runtime: dict | None,
    rows: dict[str, dict],
    incomplete: bool,
) -> tuple[str | None, str]:
    patterns = target["inputs"] + policy["shared_inputs"]
    matches = {path: digest for path, digest in files.items() if _matches_any(patterns, path)}
    missing = any(not any(_matches(pattern, path) for path in files) for pattern in patterns)
    deleted = any(path not in files and _matches_any(patterns, path) for path in paths)
    producers = {key: rows[key]["cache_key"] for key in target["consumes"]}
    if not target["cache"] or target["kind"] == "deploy":
        return None, "Not declared cacheable, or a deployment side effect"
    if incomplete or missing or deleted or not _fingerprinted(runtime, producers):
        return (
            None,
            "Incomplete scope, missing/deleted input or missing runtime/producer fingerprint",
        )
    declaration = {
        key: target[key]
        for key in ("id", "command", "working_directory", "inputs", "consumes", "kind")
    }
    data = {
        "policy_version": policy["version"],
        "target": declaration,
        "inputs": matches,
        "runtime": runtime,
        "producers": producers,
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest(), "Declared-input fingerprint only; no cache was queried"


def _fingerprinted(runtime: dict | None, producers: dict[str, str | None]) -> bool:
    return (
        runtime is not None
        and bool(runtime)
        and all(value is not None for value in runtime.values())
        and all(value is not None for value in producers.values())
    )


def _review_reasons(paths: list[str], shared: list[str], uncovered: list[str]) -> list[str]:
    configs = [
        path
        for path in paths
        if path.startswith((".github/", ".circleci/"))
        or path
        in {"anaxigraph/ci-policy.json", ".gitlab-ci.yml", "azure-pipelines.yml", "Jenkinsfile"}
    ]
    result = [f"CI/task configuration changed: {path}" for path in configs[:10]]
    result.extend(f"Shared input changed: {path}" for path in shared[:10])
    if uncovered:
        result.append(
            "Declare ownership or a relevant check target for uncovered changes; validate that narrower scope before removing the fallback"
        )
    return result


def _uncovered(
    policy: dict, paths: list[str], targets: dict, reachable: set, shared: list[str]
) -> tuple[list[str], list[str]]:
    owners = {
        path: {key for key, item in targets.items() if _matches_any(item["inputs"], path)}
        for path in paths
    }
    ignored = [
        path
        for path in paths
        if not owners[path] and _matches_any(policy["ignored_inputs"], path) and path not in shared
    ]
    uncovered = [
        path
        for path in paths
        if path not in ignored
        and not (path in shared and reachable)
        and not owners[path].intersection(reachable)
    ]
    return uncovered, ignored


def _selection(
    policy: dict, paths: list[str], targets: dict, eligible: set, cadence: str
) -> tuple[set, dict, list[str], list[str], list[str]]:
    shared = [path for path in paths if _matches_any(policy["shared_inputs"], path)]
    affected, reasons = _invalidation(targets, paths, shared)
    reachable = _coverage(targets, eligible | set(policy["always"]), _layers(targets, set(targets)))
    uncovered, ignored = _uncovered(policy, paths, targets, reachable, shared)
    selected = (affected.intersection(eligible) if cadence == "change" else set(eligible)) | set(
        policy["always"]
    )
    if cadence != "change":
        for key in eligible:
            reasons[key].append(f"Explicit {cadence} cadence obligation")
    for key in policy["always"]:
        reasons[key].append("Explicit always-retained check")
    if uncovered:
        selected.update(policy["fallback"])
        for key in policy["fallback"]:
            reasons[key].append("Declared fallback for unresolved change ownership")
    _prerequisites(targets, selected, reasons)
    return selected, reasons, shared, uncovered, ignored


def _tasks(
    policy: dict,
    targets: dict,
    selected: set,
    reasons: dict,
    files: dict,
    paths: list[str],
    runtime: dict | None,
    incomplete: bool,
) -> tuple[list[dict], list[list[str]], float | None]:
    layers = _layers(targets, selected)
    rows: dict[str, dict] = {}
    finishes: dict[str, float] = {}
    for key in (key for layer in layers for key in layer):
        target = targets[key]
        cache_key, cache_reason = _cache(target, policy, files, paths, runtime, rows, incomplete)
        rows[key] = target | {
            "reasons": sorted(set(reasons[key])),
            "cache_key": cache_key,
            "cache_eligible": cache_key is not None,
            "cache_reason": cache_reason,
        }
        parents = target["needs"] + target["consumes"]
        finishes[key] = (target["estimated_seconds"] or 0) + max(
            (finishes[parent] for parent in parents), default=0
        )
    known = all(targets[key]["estimated_seconds"] is not None for key in selected)
    return list(rows.values()), layers, max(finishes.values(), default=0) if known else None


def _excluded(targets: dict, selected: set, eligible: set) -> list[dict]:
    return [
        {
            "id": key,
            "reason": "Different cadence"
            if key not in eligible
            else "No changed declared data inputs",
        }
        for key in targets
        if key not in selected
    ]


def _retained(targets: dict, selected: set, cadence: str) -> list[str]:
    return [
        key
        for key, target in targets.items()
        if target["cadence"] == cadence and key not in selected
    ]


def plan_ci(
    policy: Any,
    changed_paths: list[str],
    files: dict[str, str],
    *,
    cadence: str = "change",
    runtime_context: dict | None = None,
) -> dict:
    policy = validate_policy(policy)
    paths = sorted(_path(path) for path in _strings(changed_paths, "changed_paths", 20_000))
    if cadence not in tuple(CADENCES):
        raise ValueError("CI cadence must be change, release or scheduled")
    runtime = _runtime(runtime_context)
    targets = {item["id"]: item for item in policy["targets"]}
    eligible = {key for key, item in targets.items() if item["cadence"] == cadence}
    selected, reasons, shared, uncovered, ignored = _selection(
        policy, paths, targets, eligible, cadence
    )
    rows, layers, critical = _tasks(
        policy, targets, selected, reasons, files, paths, runtime, bool(uncovered)
    )
    review = _review_reasons(paths, shared, uncovered)
    return {
        "status": "incomplete" if uncovered else "planned",
        "execution_ready": not uncovered,
        "execution_note": "Complete declared selection only; commands and deployments were not run.",
        "cadence": cadence,
        "changed_paths": paths,
        "selected_targets": rows,
        "excluded_targets": _excluded(targets, selected, eligible),
        "layers": layers,
        "uncovered_paths": uncovered,
        "ignored_paths": ignored,
        "retained_periodic": _retained(targets, selected, "scheduled"),
        "retained_release": _retained(targets, selected, "release"),
        "expansion_reasons": sorted(
            {
                reason
                for values in reasons.values()
                for reason in values
                if not reason.startswith("Changed input:")
            }
        ),
        "reevaluation_required": bool(review),
        "reevaluation_reasons": review,
        "critical_path_seconds": critical,
        "critical_path_basis": "Lower bound from declared estimates, unlimited workers; queue and undeclared setup waits unknown.",
        "savings_seconds": None,
        "assurance": "Declared policy and supplied fingerprints; not observed coverage, a cache hit or passed checks.",
    }
