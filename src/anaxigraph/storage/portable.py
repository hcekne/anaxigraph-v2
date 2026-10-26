"""One Git-carried current record, without databases, source or rebuildable indexes.

The application validates interpretations before importing them. This component owns
the portable envelope, content identity, size bound and atomic file publication.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from anaxigraph.contracts import Graph

FORMAT = "anaxigraph-record-v1"
MAX_RECORD_BYTES = 100_000_000
ANNOTATION_FIELDS = {
    "node_id",
    "overview",
    "detail",
    "capabilities",
    "functions",
    "contracts",
    "uncertainty",
    "producer",
    "basis",
    "source_snapshot",
    "context_fingerprint",
    "request_id",
    "provenance",
    "execution",
    "annotation_contract",
    "dependency_implementation_fingerprint",
}


def encode(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def content_identity(files: dict[str, str]) -> str:
    """Path-relative source identity survives a commit or a clone at another path."""
    return hashlib.sha256(encode(files)).hexdigest()


def record_path(root: Path, *, create: bool = False) -> Path:
    folder = root / "anaxigraph"
    target = folder / "record.json"
    if folder.is_symlink() or target.is_symlink():
        raise ValueError("Portable records cannot use symlinked paths")
    if create:
        folder.mkdir(exist_ok=True)
    return target


def read_record(root: Path, *, maximum: int = MAX_RECORD_BYTES) -> dict[str, Any] | None:
    path = record_path(root)
    if not path.exists():
        return None
    if path.stat().st_size > maximum:
        raise ValueError("Portable record exceeds its configured size budget")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Portable record grew beyond its configured size budget")
    try:
        record = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise ValueError(
            "Portable record is not valid JSON; the existing file was preserved"
        ) from error
    if not isinstance(record, dict) or record.get("format") != FORMAT:
        raise ValueError("Unsupported portable record format")
    if not isinstance(record.get("modules"), list) or not isinstance(record.get("analyses"), list):
        raise ValueError("Portable record must contain module and analysis lists")
    source = record.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("files"), dict):
        raise ValueError("Portable record must declare its source fingerprints")
    files = source["files"]
    if any(not isinstance(p, str) or not isinstance(h, str) for p, h in files.items()):
        raise ValueError("Portable source fingerprints must be text")
    if content_identity(files) != source.get("identity"):
        raise ValueError("Portable source identity does not match its fingerprints")
    return record


def _analysis_key(row: dict[str, Any]) -> str:
    # Current findings replace their previous version; Git supplies the history.
    body = row.get("body", {})
    return hashlib.sha256(
        encode(
            {
                "kind": row.get("kind"),
                "scope": sorted(body.get("scope", [])),
                "pattern_ids": body.get("request_options", {}).get("pattern_ids"),
                "level": body.get("request_options", {}).get("level"),
                "saved_id": row.get("id") if row.get("explicitly_saved") else None,
            }
        )
    ).hexdigest()


def build_record(
    graph: Graph, reviews: list[dict[str, Any]], existing: dict[str, Any] | None = None
) -> dict[str, Any]:
    source_id = content_identity(graph.snapshot.files)
    module_ids = {n.id for n in graph.nodes if n.kind == "module"}
    modules = {
        row["id"]: row | {"status": "stale"}
        for row in (existing or {}).get("modules", [])
        if isinstance(row, dict) and row.get("id") in module_ids
    }
    for node in graph.nodes:
        annotation = node.data.get("annotation")
        if node.kind != "module" or not annotation:
            continue
        modules[node.id] = {
            "id": node.id,
            "path": node.path,
            "fingerprint": node.data.get("annotation_fingerprint", graph.snapshot.files[node.path]),
            "status": node.data.get("annotation_status", "unknown"),
            "annotation": {
                key: value for key, value in annotation.items() if key in ANNOTATION_FIELDS
            },
        }
    # Preserve saved findings across cache retention and subsequent incompatible scans.
    # Never restore an excluded/removed module's historical material as current scope.
    analyses: dict[str, dict[str, Any]] = {}
    previous = (existing or {}).get("analyses", [])
    for row in previous:
        if not isinstance(row, dict) or not isinstance(row.get("body"), dict):
            continue
        if set(row["body"].get("scope", [])) <= module_ids or row.get("explicitly_saved"):
            if row.get("source_identity") != source_id:
                row = row | {"status": "stale"}
            analyses[_analysis_key(row)] = row
    for review in reviews:
        if review["kind"] == "request":
            continue
        body = review["result"]
        if not set(body.get("scope", [])) <= module_ids:
            continue
        item = {
            "id": review["id"],
            "kind": review["kind"],
            "status": review["status"],
            "source_identity": source_id if review["status"] == "current" else None,
            "source_snapshot": review["snapshot_id"],
            "created_at": review["created_at"],
            "body": body,
        }
        key = _analysis_key(item)
        prior = analyses.get(key)
        if prior and prior.get("id") == item["id"] and item["source_identity"] is None:
            item["source_identity"] = prior.get("source_identity")
        analyses[key] = item
    return {
        "format": FORMAT,
        "source": {
            "identity": source_id,
            "revision": graph.snapshot.revision,
            "files": dict(sorted(graph.snapshot.files.items())),
        },
        "modules": sorted(modules.values(), key=lambda row: row["path"]),
        "analyses": sorted(analyses.values(), key=_analysis_key),
    }


def write_record(
    root: Path, record: dict[str, Any], *, maximum: int = MAX_RECORD_BYTES
) -> dict[str, Any]:
    raw = encode(record)
    if len(raw) > maximum:
        raise ValueError(
            "Portable record exceeds its size budget; the last valid record was preserved"
        )
    path = record_path(root, create=True)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = f".record-{uuid.uuid4().hex}.tmp"
    changed = True
    try:
        try:
            old = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:
            old = None
        if old is not None:
            with os.fdopen(old, "rb") as stream:
                changed = (
                    os.fstat(stream.fileno()).st_size != len(raw)
                    or stream.read(len(raw) + 1) != raw
                )
        if changed:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o644,
                dir_fd=directory,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # Directory-relative publication cannot follow a swapped parent symlink.
            os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        actual, intended = path.parent.lstat(), os.fstat(directory)
        if (actual.st_dev, actual.st_ino) != (intended.st_dev, intended.st_ino):
            raise ValueError("Portable record location changed during publication")
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)
    return {
        "path": str(path),
        "bytes": len(raw),
        "files": 1,
        "changed": changed,
        "record_exported": True,
        "committed_pushed": False,
    }


def record_status(root: Path) -> dict[str, Any]:
    path = record_path(root)
    return {
        "path": str(path),
        "exists": path.is_file(),
        "bytes": path.stat().st_size if path.is_file() else 0,
        "files": int(path.is_file()),
        "committed_pushed": "not_checked",
    }
