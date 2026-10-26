"""Import and publish portable understanding through shared application state."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from anaxigraph.analysis import validate_result
from anaxigraph.analysis.portable import compatible_annotations, input_identity
from anaxigraph.application.context import context_fingerprints
from anaxigraph.storage.portable import (
    analysis_contexts,
    build_record,
    content_identity,
    read_record,
    record_status,
    write_record,
)

if TYPE_CHECKING:
    from anaxigraph.application.core import Application


def portable_status(app: Application) -> dict[str, Any]:
    return record_status(app.root)


def _portable_token(app: Application) -> tuple:
    state = app.portable_status()
    path = Path(state["path"])
    record = path.stat() if state["exists"] else None
    return (
        app.store.knowledge_token(str(app.root)),
        app.store.review_token(str(app.root)),
        (record.st_ino, record.st_mtime_ns, record.st_size) if record else None,
    )


def export_record(app: Application) -> dict[str, Any]:
    graph = app._current()
    app._require_live(graph)
    token = app._portable_token()
    cached = getattr(app, "_export_cache", None)
    if cached and cached[0] == token:
        # A commit/push can change publication state without touching the record.
        return cached[1] | {"changed": False} | app.portable_status()
    reviews = []
    for row in app.store.reviews(str(app.root), include_requests=False):
        if row["kind"] == "request":
            continue
        row = app._review_state(row, graph)
        if "input_identity" not in row["result"]:
            original = app.store.get_review(str(app.root), row["id"], "request")
            if original:
                row = row | {
                    "result": row["result"]
                    | {
                        "input_identity": input_identity(original["result"]),
                        "request_options": original["result"].get("request_options", {}),
                        "children": original["result"].get("children", []),
                    }
                }
        reviews.append(row)
    local_bytes = app.store.path.stat().st_size
    ledger = app.root / ".anaxigraph-v2" / "trial.sqlite"
    if ledger.is_file():
        local_bytes += ledger.stat().st_size
    directory = app.root / ".anaxigraph-v2" / "workflows"
    if directory.is_dir() and not directory.is_symlink():
        local_bytes += sum(path.stat().st_size for path in directory.iterdir() if path.is_file())
    result = write_record(
        app.root,
        build_record(graph, reviews, read_record(app.root)),
        maximum=max(0, 100_000_000 - local_bytes),
    )
    current = app._portable_token()
    if current[:2] == token[:2]:
        app._export_cache = (current, result)
        app._import_cache = current
    else:
        app._export_cache = None
        app._import_cache = None
    return result


def import_record(app: Application) -> dict[str, Any]:
    token = app._portable_token()
    if getattr(app, "_import_cache", None) == token:
        return {
            "annotations_imported": 0,
            "analyses_imported": 0,
            "record_found": app.portable_status()["exists"],
            "unchanged": True,
        }
    record = read_record(app.root)
    if record is None:
        return {"annotations_imported": 0, "analyses_imported": 0, "record_found": False}
    graph = app._current()
    annotations, skipped = compatible_annotations(record, graph, context_fingerprints(graph))
    app._require_live(graph)
    modules = {node.id: node for node in graph.nodes if node.kind == "module"}
    for annotation in annotations:
        prior = modules[annotation["node_id"]].data.get("annotation")
        expected = hashlib.sha256(
            json.dumps(prior, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        app.store.annotate(
            str(app.root),
            graph.snapshot.id,
            annotation["node_id"],
            annotation,
            expected_digest=expected,
        )
    graph = app._current()
    source_id = content_identity(graph.snapshot.files)
    contexts = analysis_contexts(graph)
    pending = [
        row
        for row in record["analyses"]
        if row.get("status") == "current"
        and (
            row.get("source_identity") == source_id
            or row.get("analysis_context") == contexts(row.get("body", {}).get("scope", []))
        )
    ]
    imported = 0
    remapped: dict[str, str] = {}
    while pending:
        deferred, progress = [], False
        for row in pending:
            body = row["body"]
            if any(child["request_id"] not in remapped for child in body.get("children", [])):
                deferred.append(row)
                continue
            candidate = body | {
                "children": [
                    child | {"request_id": remapped[child["request_id"]]}
                    for child in body.get("children", [])
                ]
            }
            try:
                request = app._rebuild_request(graph, row["kind"], candidate)
                if input_identity(request) != body.get("input_identity"):
                    skipped.append({"id": row["id"], "reason": "analysis inputs changed"})
                    continue
                validate_result(request, body["result"], graph)
            except ValueError as error:
                skipped.append({"id": row["id"], "reason": str(error)[:200]})
                continue
            identity = request["request_id"]
            remapped[row["id"]] = identity
            progress = True
            if not app.analysis_saved(identity):
                app.store.save_review(
                    str(app.root), graph.snapshot.id, identity, "request", request
                )
                app.store.save_review(
                    str(app.root),
                    graph.snapshot.id,
                    identity,
                    row["kind"],
                    candidate
                    | {
                        "provenance": {
                            "original_id": row["id"],
                            "source_snapshot": row["source_snapshot"],
                        }
                    },
                )
                imported += 1
        if not progress:
            skipped.extend(
                {"id": row["id"], "reason": "missing compatible child analysis"} for row in deferred
            )
            break
        pending = deferred
    app._import_cache = app._portable_token()
    return {
        "annotations_imported": len(annotations),
        "analyses_imported": imported,
        "record_found": True,
        "skipped": skipped[:30],
        "skipped_count": len(skipped),
        "incompatible_analyses_preserved": len(record["analyses"]) - imported,
    }
