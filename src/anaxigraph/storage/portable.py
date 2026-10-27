"""One Git-carried current record, without databases, source or rebuildable indexes.

The application validates interpretations before importing them. This component owns
the portable envelope, content identity, size bound and atomic file publication.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from collections.abc import Callable
from functools import lru_cache
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


def analysis_contexts(graph: Graph) -> Callable[[list[str]], str]:
    """Guard opaque resources while scoped requests validate parsed source facts."""
    parsed = {
        node.path
        for node in graph.nodes
        if node.kind == "module" and node.data.get("parse_status") == "parsed"
    }
    resources = {
        path: fingerprint
        for path, fingerprint in graph.snapshot.files.items()
        if path not in parsed and Path(path).suffix.lower() not in {".md", ".rst", ".adoc"}
    }
    resource_id = content_identity(resources)
    documents = {
        node.id: {
            item["path"]: graph.snapshot.files[item["path"]]
            for contract in (node.data.get("annotation") or {}).get("contracts", [])
            for item in contract.get("evidence", [])
            if item["path"] in graph.snapshot.files
            and Path(item["path"]).suffix.lower() in {".md", ".rst", ".adoc"}
        }
        for node in graph.nodes
        if node.kind == "module"
    }

    def identity(scope: list[str]) -> str:
        evidence = {path: value for key in scope for path, value in documents.get(key, {}).items()}
        return hashlib.sha256(
            encode({"policy": 1, "resources": resource_id, "evidence": evidence})
        ).hexdigest()

    return identity


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


def retention_options(body: dict[str, Any]) -> dict[str, Any]:
    """Only logical retention forgets default shortlists; model inputs stay exact."""
    options = body.get("request_options", {})
    return (
        options | {"pattern_ids": "current-screened-selection"}
        if body.get("pattern_mode") == "screened"
        else options
    )


def _analysis_key(row: dict[str, Any]) -> str:
    # Current findings replace their previous version; Git supplies the history.
    body = row.get("body", {})
    return hashlib.sha256(
        encode(
            {
                "kind": row.get("kind"),
                "scope": sorted(body.get("scope", [])),
                "pattern_ids": retention_options(body).get("pattern_ids"),
                "level": body.get("request_options", {}).get("level"),
                "saved_id": row.get("id") if row.get("explicitly_saved") else None,
                **(
                    {
                        "pipeline_paths": body.get("request_options", {}).get("pipeline_paths")
                        or None
                    }
                    if row.get("kind") == "delivery"
                    else {}
                ),
                **(
                    {"lens": body.get("request_options", {}).get("lens")}
                    if row.get("kind") == "architecture"
                    else {}
                ),
            }
        )
    ).hexdigest()


def _annotation_meaning(annotation: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in annotation.items()
        if key not in {"source_snapshot", "request_id", "provenance"}
    }


def _same_analysis(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    old, new = previous["body"], current["body"]
    return bool(
        current["status"] == "current"
        and (
            previous.get("source_identity") == current["source_identity"]
            or (
                current.get("analysis_context") is not None
                and previous.get("analysis_context") == current["analysis_context"]
            )
        )
        and new.get("input_identity")
        and old.get("input_identity") == new["input_identity"]
        and old.get("result") == new.get("result")
        and [child["result_digest"] for child in old.get("children", [])]
        == [child["result_digest"] for child in new.get("children", [])]
    )


def _module_records(graph: Graph, existing: dict[str, Any], module_ids: set[str]) -> dict[str, Any]:
    modules = {
        row["id"]: row | {"status": "stale"}
        for row in (existing or {}).get("modules", [])
        if isinstance(row, dict) and row.get("id") in module_ids
    }
    for node in graph.nodes:
        annotation = node.data.get("annotation")
        if node.kind != "module" or not annotation:
            continue
        current: dict[str, Any] = {
            "id": node.id,
            "path": node.path,
            "fingerprint": node.data.get("annotation_fingerprint", graph.snapshot.files[node.path]),
            "status": node.data.get("annotation_status", "unknown"),
            "annotation": {key: annotation[key] for key in annotation.keys() & ANNOTATION_FIELDS},
        }
        previous = modules.get(node.id)
        if (
            previous
            and previous["fingerprint"] == current["fingerprint"]
            and _annotation_meaning(previous["annotation"])
            == _annotation_meaning(current["annotation"])
        ):
            current = previous | {"status": current["status"]}
        modules[node.id] = current
    return modules


def _retained_analyses(
    existing: dict[str, Any], module_ids: set[str], source_id: str
) -> dict[str, Any]:
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
    return analyses


def _promoted_analysis(analyses: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    """Retag an identical shared row without inventing history or duplicating its result."""
    body = dict(item["body"])
    mode = body.pop("pattern_mode", None)
    if mode not in ("screened", "selected"):
        return None
    if mode == "selected":
        body["pattern_mode"] = "screened"
    legacy_key = _analysis_key(item | {"body": body})
    previous = analyses.get(legacy_key)
    if (
        previous
        and previous["body"].get("pattern_mode") != "selected"
        and not previous.get("explicitly_saved")
        and _same_analysis(previous, item)
    ):
        del analyses[legacy_key]
        return previous | {"body": previous["body"] | {"pattern_mode": mode}}
    return None


def _analysis_records(
    reviews: list[dict[str, Any]],
    existing: dict[str, Any],
    module_ids: set[str],
    source_id: str,
    contexts: Callable[[list[str]], str],
) -> dict[str, Any]:
    analyses = _retained_analyses(existing, module_ids, source_id)
    canonical_ids: dict[str, str] = {}
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
            "analysis_context": contexts(body.get("scope", []))
            if review["status"] == "current"
            else None,
            "source_snapshot": review["snapshot_id"],
            "created_at": review["created_at"],
            "body": body,
        }
        key = _analysis_key(item)
        promoted = _promoted_analysis(analyses, item)
        prior = analyses.get(key) or promoted
        if prior and _same_analysis(prior, item):
            item = prior | {"status": item["status"], "analysis_context": item["analysis_context"]}
        if body.get("pattern_mode") in ("screened", "selected"):
            item = item | {"body": item["body"] | {"pattern_mode": body["pattern_mode"]}}
        if prior and prior.get("id") == item["id"] and item["source_identity"] is None:
            item["source_identity"] = prior.get("source_identity")
        analyses[key] = item
        canonical_ids[review["id"]] = item["id"]
    for key, item in analyses.items():
        body = item["body"]
        if body.get("children"):
            analyses[key] = item | {
                "body": body
                | {
                    "children": [
                        child
                        | {
                            "request_id": canonical_ids.get(
                                child["request_id"], child["request_id"]
                            )
                        }
                        for child in body["children"]
                    ]
                }
            }
    return analyses


def build_record(
    graph: Graph, reviews: list[dict[str, Any]], existing: dict[str, Any] | None = None
) -> dict[str, Any]:
    existing = existing or {}
    source_id = content_identity(graph.snapshot.files)
    module_ids = {node.id for node in graph.nodes if node.kind == "module"}
    modules = _module_records(graph, existing, module_ids)
    analyses = _analysis_records(reviews, existing, module_ids, source_id, analysis_contexts(graph))
    previous_source = (existing or {}).get("source", {})
    revision = (
        previous_source.get("revision")
        if previous_source.get("identity") == source_id
        else graph.snapshot.revision
    )
    return {
        "format": FORMAT,
        "source": {
            "identity": source_id,
            "revision": revision,
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
        **_publication_status(root, path),
    }


def record_status(root: Path) -> dict[str, Any]:
    path = record_path(root)
    return {
        "path": str(path),
        "exists": path.is_file(),
        "bytes": path.stat().st_size if path.is_file() else 0,
        "files": int(path.is_file()),
        **_publication_status(root, path),
    }


def _git(root: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments], capture_output=True, text=True, timeout=3
        )
        return result.stdout.rstrip("\r\n") if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


@lru_cache(maxsize=32)
def _record_blob(root: str, path: str, signature: tuple[int, ...]) -> str | None:
    # Ref changes are measured each call; only a file's verified byte hash is reused.
    value = _git(Path(root), "hash-object", "--no-filters", "--", path)
    try:
        actual = Path(path).stat()
    except OSError:
        return None
    after = (actual.st_dev, actual.st_ino, actual.st_size, actual.st_mtime_ns, actual.st_ctime_ns)
    return value if signature == after else None


def _publication_status(root: Path, path: Path) -> dict[str, Any]:
    """Measure the current record against Git and disclose the local-ref proof.

    This performs no fetch or push. A matching remote-tracking ref is useful
    last-known evidence, not a claim that the remote was contacted just now.
    """
    result: dict[str, Any] = {
        "tracked": None,
        "committed": None,
        "pushed": None,
        "committed_pushed": None,
        "publication": {"basis": "unavailable", "remote_contacted": False},
    }
    prefix = _git(root, "rev-parse", "--show-prefix")
    if prefix is None:
        return result
    relative = "anaxigraph/record.json"
    tracked = _git(root, "ls-files", "--error-unmatch", "--", relative) is not None
    result["tracked"] = tracked
    if not path.is_file():
        return result | {"committed": False, "committed_pushed": False}
    state = path.stat()
    signature = (state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns)
    blob = _record_blob(str(root), str(path), signature)
    if blob is None:
        return result
    head_blob = _git(root, "rev-parse", "--verify", f"HEAD:{prefix}{relative}")
    committed = blob == head_blob
    upstream = _git(root, "rev-parse", "--symbolic-full-name", "@{upstream}")
    result.update(committed=committed, committed_pushed=False if not committed else None)
    result["publication"] = {"basis": "local_commit", "remote_contacted": False}
    if upstream is None or not upstream.startswith("refs/remotes/"):
        return result
    if _git(root, "rev-parse", "--verify", f"{upstream}^{{commit}}") is None:
        return result
    upstream_blob = _git(root, "rev-parse", "--verify", f"{upstream}:{prefix}{relative}")
    reachable = _git(root, "merge-base", "--is-ancestor", "HEAD", upstream) is not None
    matches = blob == upstream_blob
    pushed = committed and reachable and matches
    return result | {
        "pushed": pushed,
        "committed_pushed": committed and pushed,
        "publication": {
            "basis": "local_tracking_ref",
            "remote_contacted": False,
            "upstream_ref": upstream,
            "head_in_upstream": reachable,
            "record_in_upstream": matches,
        },
    }
