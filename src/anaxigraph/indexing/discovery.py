"""Read repository inputs without importing or executing the analyzed project."""

from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import os
import subprocess
import tokenize
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from anaxigraph.contracts import Snapshot

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    ".anaxigraph",
    ".anaxigraph-v2",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "htmlcov",
    "test-results",
    "playwright-report",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".db", ".db-shm", ".db-wal"}
MAX_INPUT_BYTES = 2_000_000
MAX_SOURCE_PACKET_BYTES = MAX_INPUT_BYTES
INDEX_VERSION = "python-ast-v3-public-surface"


@dataclass
class SourceCache:
    """Bounded process-local captures, never trusted from another checkout.

    Every request still enumerates eligible paths and checks regular-file metadata
    including ctime and inode. Only byte reading, decoding and hashing are reused.
    Moving a symlink into an ancestor cannot hit a cached regular-file entry.
    """

    maximum_bytes: int = 32_000_000
    entries: dict[str, tuple[tuple[int, ...], str | None, str | None, tuple[str, ...]]] = field(
        default_factory=dict
    )
    bytes_read: int = 0
    files_read: int = 0
    files_reused: int = 0

    def begin(self) -> None:
        self.bytes_read = self.files_read = self.files_reused = 0


def _signature(root: Path, relative: str) -> tuple[int, ...] | None:
    path = _regular_input(root, relative)
    if path is None:
        return None
    try:
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
    except OSError:
        return None


def _git(root: Path, *args: str) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, timeout=20, check=False
        )
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _portable_file(parts: tuple[str, ...]) -> bool:
    return parts in {("anaxigraph", "record.json"), ("anaxigraph", "config.json")} or (
        len(parts) == 2
        and parts[0] == "anaxigraph"
        and parts[1].startswith(".record-")
        and parts[1].endswith(".tmp")
    )


def _excluded(path: str, patterns: tuple[str, ...]) -> bool:
    parts = Path(path).parts
    if not parts or _portable_file(parts):
        return True
    if any(part in EXCLUDED_DIRS or part.endswith(".egg-info") for part in parts):
        return True
    if any(path.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
        return True
    return any(
        fnmatch.fnmatchcase(path, pattern.rstrip("/"))
        or path.startswith(pattern.rstrip("/") + "/")
        or (
            "/" not in pattern.rstrip("/")
            and any(fnmatch.fnmatchcase(part, pattern.rstrip("/")) for part in parts)
        )
        for pattern in patterns
    )


def _regular_input(root: Path, relative: str) -> Path | None:
    """Reject symlinks at every component, including cached Git directory entries."""
    candidate = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        return None
    current = root
    try:
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                return None
        resolved = candidate.resolve(strict=True)
        return resolved if resolved.is_relative_to(root) and resolved.is_file() else None
    except (OSError, RuntimeError):
        return None


def _paths(listing: bytes | None) -> list[str]:
    return [os.fsdecode(path) for path in (listing or b"").split(b"\0") if path]


def _legacy_record_patterns(root: Path) -> tuple[str, ...]:
    """Recognize old generated partitions without hiding a same-named package.

    Legacy marker files are tiny. Large current records do not need decoding:
    their one reserved JSON path is already excluded above. Python/JS source
    under these directories remains eligible even alongside legacy metadata.
    """
    marker = _regular_input(root, "anaxigraph/record.json")
    if marker is None or marker.stat().st_size > 4096:
        return ()
    try:
        with marker.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            return ()
        value = json.loads(raw)
    except (OSError, ValueError, UnicodeError):
        return ()
    if not isinstance(value, dict) or value.get("format") not in {
        "anaxigraph-record-v2",
        "anaxigraph-record-v3",
        "anaxigraph-record-v4",
    }:
        return ()
    generated = tuple(
        f"anaxigraph/{directory}/*{suffix}"
        for directory in ("mapping", "analyses", "views")
        for suffix in (".json", ".jsonl", ".gz", ".xz", ".md", ".html", ".svg", ".txt")
    )
    return ("anaxigraph/manifest.json", *generated)


def _walk_files(root: Path, excluded: tuple[str, ...]) -> list[str]:
    candidates: list[str] = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        dirs[:] = [name for name in dirs if not _excluded((relative / name).as_posix(), excluded)]
        candidates.extend((relative / name).as_posix() for name in files)
    return sorted(candidates)


def _inventory(
    root: Path, excluded: tuple[str, ...], diagnostics: list[str]
) -> tuple[list[str], str | None, bool]:
    """Choose Git-tracked/untracked inputs and report dirty state in the same scope."""
    listing = _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", ".")
    revision = _git(root, "rev-parse", "HEAD")
    changed = _git(root, "diff", "--name-only", "--relative", "-z", "HEAD", "--", ".")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", ".")
    if listing is not None:
        candidates = sorted(set(_paths(listing)))
    else:
        diagnostics.append(
            "No Git inventory available; scanning regular files with default exclusions."
        )
        candidates = _walk_files(root, excluded)
    dirty_paths = _paths(changed) + _paths(untracked)
    if listing is not None and revision is None:
        dirty_paths.extend(candidates)  # New Git repository without its first commit.
    dirty = any(not _excluded(path, excluded) for path in dirty_paths)
    return candidates, revision.decode().strip() if revision else None, dirty


def _read_input(
    root: Path, relative: str, diagnostics: list[str], maximum: int = MAX_INPUT_BYTES
) -> bytes | None:
    """Capture one bounded regular input; do not silently truncate parser source."""
    path = _regular_input(root, relative)
    if path is None:
        if (root / relative).exists():
            diagnostics.append(f"{relative}: not a regular repository input; not analyzed.")
        return None
    try:
        if path.stat().st_size > maximum:
            diagnostics.append(f"{relative}: exceeds {maximum}-byte input limit; not analyzed.")
            return None
        with path.open("rb") as stream:
            content = stream.read(maximum + 1)
        if len(content) > maximum:
            raise ValueError(f"{relative}: file grew beyond input limit during capture; rescan.")
        return content
    except OSError as exc:
        diagnostics.append(f"{relative}: input could not be read ({type(exc).__name__}).")
        return None


def capture_sources(
    root: Path,
    paths: list[str],
    fingerprints: dict[str, str],
    *,
    maximum: int = MAX_SOURCE_PACKET_BYTES,
) -> dict[str, str]:
    """Read exact mapped source for agent packets without following symlinks or executing code.

    The request builder applies its configurable aggregate packet budget. Decode Python
    coding cookies from the same bounded bytes whose fingerprint was checked; never replace an
    invalid sequence silently or return partial source.
    """
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Repository root must be a directory")
    if type(maximum) is not int or not 1 <= maximum <= MAX_INPUT_BYTES:
        raise ValueError(f"Source input budget must be between 1 and {MAX_INPUT_BYTES} bytes")
    sources = {}
    for relative in paths:
        if relative not in fingerprints:
            raise ValueError("Source path must belong to the analyzed snapshot")
        diagnostics: list[str] = []
        raw = _read_input(root, relative, diagnostics, maximum)
        if raw is None:
            raise ValueError(f"Source could not be captured: {relative}; {diagnostics}")
        if hashlib.sha256(raw).hexdigest() != fingerprints[relative]:
            raise ValueError("Source changed after mapping; scan again before annotation")
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
            sources[relative] = raw.decode(encoding)
        except (UnicodeError, SyntaxError) as exc:
            raise ValueError(f"Source encoding could not be decoded: {relative}") from exc
    return sources


def _capture_inputs(
    root: Path,
    candidates: list[str],
    excluded: tuple[str, ...],
    diagnostics: list[str],
    cache: SourceCache | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Fingerprint captured bytes and decode supported Python inputs from those exact bytes."""
    files: dict[str, str] = {}
    contents: dict[str, str] = {}
    binary_count = 0
    next_entries = {}
    cache_bytes = 0
    if cache is not None:
        cache.begin()
    for relative in candidates:
        if _excluded(relative, excluded):
            continue
        signature = _signature(root, relative) if cache is not None else None
        saved = cache.entries.get(relative) if cache is not None else None
        if saved is not None and signature == saved[0]:
            assert cache is not None  # A saved entry can only come from this cache.
            _, fingerprint, source, errors = saved
            if fingerprint is None:
                binary_count += 1
            else:
                files[relative] = fingerprint
                if source is not None:
                    contents[relative] = source
            diagnostics.extend(errors)
            next_entries[relative] = saved
            cache_bytes += len(source.encode()) if source is not None else 0
            cache.files_reused += 1
            continue
        before_errors = len(diagnostics)
        content = _read_input(root, relative, diagnostics)
        if content is None:
            continue
        if cache is not None:
            cache.bytes_read += len(content)
            cache.files_read += 1
        suffix = Path(relative).suffix
        if b"\0" in content[:8192] and suffix not in {".py", ".pyi"}:
            binary_count += 1
            if cache is not None and signature is not None:
                next_entries[relative] = (signature, None, None, ())
            continue
        files[relative] = hashlib.sha256(content).hexdigest()
        try:
            if suffix in {".py", ".pyi"}:
                encoding, _ = tokenize.detect_encoding(io.BytesIO(content).readline)
                contents[relative] = content.decode(encoding)
        except (UnicodeError, SyntaxError) as exc:
            diagnostics.append(f"{relative}: input could not be read ({type(exc).__name__}).")
        if cache is not None and signature is not None:
            if _signature(root, relative) != signature:
                raise ValueError(f"{relative}: source changed during capture; rescan.")
            source = contents.get(relative)
            size = len(source.encode()) if source is not None else 0
            if cache_bytes + size <= cache.maximum_bytes:
                next_entries[relative] = (
                    signature,
                    files[relative],
                    source,
                    tuple(diagnostics[before_errors:]),
                )
                cache_bytes += size
    if cache is not None:
        # New entries can precede reused entries in sorted inventory order. Bound
        # the final set too, rather than letting those later cache hits exceed it.
        retained, used = {}, 0
        for relative, entry in next_entries.items():
            size = len(entry[2].encode()) if entry[2] is not None else 0
            if used + size <= cache.maximum_bytes:
                retained[relative] = entry
                used += size
        cache.entries = retained
    if binary_count:
        diagnostics.append(f"{binary_count} binary files excluded from analyzed coverage.")
    unsupported = sum(
        Path(path).suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"} for path in files
    )
    if unsupported:
        diagnostics.append(
            f"{unsupported} JavaScript/TypeScript files fingerprinted but not parsed in this Python-first milestone."
        )
    return files, contents


def discover(
    root: Path, excluded: tuple[str, ...], cache: SourceCache | None = None
) -> tuple[Snapshot, dict[str, str], list[str]]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Repository root must be a directory")
    diagnostics: list[str] = []
    configured_exclusions = excluded
    excluded = tuple(sorted(set(excluded) | set(_legacy_record_patterns(root))))
    candidates, revision, dirty = _inventory(root, excluded, diagnostics)
    files, contents = _capture_inputs(root, candidates, excluded, diagnostics, cache)
    identity = {
        "root": str(root),
        "revision": revision,
        "dirty": dirty,
        "files": files,
        "excluded": sorted(configured_exclusions),
        "indexer": INDEX_VERSION,
    }
    snapshot_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return (
        Snapshot(
            id=snapshot_id,
            root=str(root),
            revision=revision,
            dirty=dirty,
            files=files,
            created_at=datetime.now(timezone.utc).isoformat(),
        ),
        contents,
        diagnostics,
    )
