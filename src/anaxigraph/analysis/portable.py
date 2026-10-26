"""Compatibility checks for portable interpretations; no file access or SQL."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from anaxigraph.analysis.validation import validate_result
from anaxigraph.contracts import Graph

from .behavior import annotation_behavior_problems

ANNOTATION_CONTRACT_VERSION = "behavior-v2"


def input_identity(request: dict[str, Any]) -> str:
    """Semantic input identity independent of checkout path and worker provenance."""

    def stable(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: stable(item)
                for key, item in value.items()
                if key not in {"request_id", "source_snapshot", "provenance"}
            }
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value

    meaning = {
        key: stable(request.get(key))
        for key in ("kind", "scope", "packet", "response_schema", "request_options")
    }
    return hashlib.sha256(
        json.dumps(
            meaning, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def compatible_annotations(
    record: dict[str, Any], graph: Graph, contexts: dict[str, str]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    modules = {node.id: node for node in graph.nodes if node.kind == "module"}
    accepted, skipped = [], []
    seen = set()
    for item in record["modules"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("Every portable module needs an identity")
        identity = item["id"]
        if identity in seen:
            raise ValueError("Portable record contains duplicate module annotations")
        seen.add(identity)
        node = modules.get(identity)
        if node is None or item.get("path") != node.path:
            skipped.append({"id": identity, "reason": "removed, excluded or unknown module"})
            continue
        annotation = item.get("annotation")
        if not isinstance(annotation, dict):
            raise ValueError("Portable annotation must be an object")
        if (
            item.get("fingerprint") != graph.snapshot.files[node.path]
            or annotation.get("context_fingerprint") != contexts[node.path]
            or annotation.get("annotation_contract") != ANNOTATION_CONTRACT_VERSION
        ):
            skipped.append({"id": identity, "reason": "source or dependency understanding changed"})
            continue
        if node.data.get("annotation_status") == "current":
            skipped.append({"id": identity, "reason": "current local understanding preserved"})
            continue
        problem = annotation_behavior_problems(graph, [annotation])
        if problem:
            skipped.append({"id": identity, "reason": next(iter(problem.values()))})
            continue
        request = {
            "kind": "annotate",
            "scope": [identity],
            "function_ids": {
                identity: [
                    n.id for n in graph.nodes if n.path == node.path and n.kind == "function"
                ]
            },
            "max_result_bytes": 8_000_000,
        }
        validate_result(
            request, {"producer": annotation.get("producer"), "annotations": [annotation]}, graph
        )
        accepted.append(
            annotation
            | {
                "source_snapshot": graph.snapshot.id,
                "provenance": annotation.get(
                    "provenance",
                    {
                        "source_snapshot": annotation.get("source_snapshot"),
                        "request_id": annotation.get("request_id"),
                        "imported_from": "anaxigraph/record.json",
                    },
                ),
            }
        )
    return accepted, skipped
