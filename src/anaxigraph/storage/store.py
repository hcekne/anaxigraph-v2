"""Bounded current knowledge, one comparison snapshot, and canonical annotations.

Only the store writes SQL. Repeated scans replace observations atomically; they
do not append a historical cache. Annotation text is stored once per source
fingerprint, with compact name/overview search documents rebuilt for the current
snapshot. Source code is never executed here.
"""

from __future__ import annotations

import json
import sqlite3
import time
import zlib
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator

from anaxigraph.contracts import Graph, Node, Snapshot
from anaxigraph.storage.graph_codec import decode_edges, decode_nodes, encode_edges, encode_nodes
from anaxigraph.storage.schema import SCHEMA, SCHEMA_VERSION


def _encode(value: Any, *, limit: int | None = None) -> str:
    result = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    if limit is not None and len(result.encode()) > limit:
        raise ValueError(f"Record exceeds the {limit:,}-byte limit")
    return result


def _root(root: str) -> str:
    return str(Path(root).resolve())


def _review_text(body: str | bytes, maximum: int) -> str:
    """Read old JSON or one bounded compressed canonical review, without temp files."""
    if isinstance(body, str):
        return body
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(body, maximum + 1)
        if len(raw) > maximum or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
            raise ValueError("Saved review exceeds its configured size limit or is incomplete")
        return raw.decode()
    except (zlib.error, UnicodeError) as exc:
        raise ValueError("Saved review is not valid compressed JSON") from exc


class Store:
    """A thread-safe connection-per-operation SQLite repository.

    The SQLite page limit bounds this complete database, including search and
    free pages. A write that would exceed it rolls back without replacing the
    last valid map. DELETE journaling avoids persistent WAL growth.
    """

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = 100_000_000,
        max_record_bytes: int | None = None,
        request_limit: int = 1024,
    ) -> None:
        if max_bytes < 65_536:
            raise ValueError("Storage budget must be at least 65,536 bytes")
        self.path = Path(path).resolve()
        self.max_bytes = max_bytes
        self.max_record_bytes = max_record_bytes or max_bytes
        if type(request_limit) is not int or request_limit < 1:
            raise ValueError("Request retention must be a positive integer")
        self.request_limit = request_limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 3, 4, 5, SCHEMA_VERSION):
                raise ValueError(f"Unsupported knowledge-store schema: {version}")
            if version in (0, 3, 4, 5):
                if version:
                    self._backup_before_migration(db)
                before = (
                    "ALTER TABLE reviews RENAME TO reviews_v3;\nDROP INDEX IF EXISTS review_kind;\n"
                    if version == 3
                    else ""
                )
                baseline_columns = {row[1] for row in db.execute("PRAGMA table_info(baselines)")}
                if baseline_columns:
                    before += "".join(
                        f"ALTER TABLE baselines ADD COLUMN {name} TEXT;\n"
                        for name in ("task_key", "revision")
                        if name not in baseline_columns
                    )
                after = (
                    "INSERT INTO reviews(root,id,kind,snapshot_id,result,created_at,updated_at) "
                    "SELECT root,id,kind,snapshot_id,result,created_at,updated_at FROM reviews_v3;\n"
                    "DROP TABLE reviews_v3;\n"
                    if version == 3
                    else ""
                )
                db.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + before
                    + SCHEMA
                    + after
                    + f"\nPRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;"
                )

    def _backup_before_migration(self, db: sqlite3.Connection) -> None:
        """Keep one recoverable original, and remove incomplete backup output."""
        backup = self.path.with_name(self.path.name + f".pre-v{SCHEMA_VERSION}.sqlite")
        if backup.exists():
            return
        temporary = backup.with_suffix(backup.suffix + ".tmp")
        try:
            with sqlite3.connect(temporary) as target:
                db.backup(target)
            temporary.replace(backup)
        finally:
            temporary.unlink(missing_ok=True)

    def knowledge_token(self, root: str) -> tuple[int, ...]:
        """Graph cache identity unaffected by request/checkpoint bookkeeping writes."""
        with self._connection() as db:
            row = db.execute(
                "SELECT version FROM knowledge_versions WHERE root=?", (_root(root),)
            ).fetchone()
        return (self.path.stat().st_ino, row[0] if row else 0)

    def review_token(self, root: str) -> tuple[int, ...]:
        """Completed proposals change independently of queued request packets."""
        with self._connection() as db:
            row = db.execute(
                "SELECT version FROM knowledge_versions WHERE root=?", ("reviews:" + _root(root),)
            ).fetchone()
        return (self.path.stat().st_ino, row[0] if row else 0)

    @staticmethod
    def _changed(db: sqlite3.Connection, root: str) -> None:
        db.execute(
            "INSERT INTO knowledge_versions VALUES (?,1) ON CONFLICT(root) "
            "DO UPDATE SET version=version+1",
            (root,),
        )

    def cache_token(self) -> tuple[int, ...]:
        """Invalidate an application's one-graph cache after any process writes.

        Call before and after reading; cache only if both tokens agree. No extra
        connection or second cache is needed, and atomic file replacement also
        invalidates the token through its inode.
        """
        state = self.path.stat()
        return (state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns)

    def pin_baseline(
        self,
        root: str,
        snapshot_id: str | None = None,
        *,
        task_key: str | None = None,
        revision: str | None = None,
        continue_task: bool = False,
    ) -> str:
        """Pin one change, rebasing for a new declared task or base revision.

        Low-level callers without task identity keep explicit-pin behavior. Application
        callers supply the task and HEAD; continuation deliberately retains the pin.
        """
        root = _root(root)
        if snapshot_id is not None and (not isinstance(snapshot_id, str) or not snapshot_id):
            raise ValueError("Baseline ID must be a nonempty snapshot identity")
        if type(continue_task) is not bool:
            raise ValueError("continue_task must be a boolean")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM baselines WHERE root=?", (root,)).fetchone()
            current = self._current(db, root)
            selected = snapshot_id or (current["current_id"] if current else None)
            if (
                snapshot_id is None
                and old
                and (
                    continue_task
                    or task_key is None
                    or (old["task_key"] == task_key and old["revision"] == revision)
                )
            ):
                selected = old["snapshot_id"]
            if (
                not selected
                or db.execute(
                    "SELECT 1 FROM snapshots WHERE root=? AND id=?", (root, selected)
                ).fetchone()
                is None
            ):
                raise ValueError("Baseline must identify a retained snapshot of this checkout")
            db.execute(
                "INSERT INTO baselines(root,snapshot_id,task_key,revision) VALUES (?,?,?,?) "
                "ON CONFLICT(root) DO UPDATE SET snapshot_id=excluded.snapshot_id,"
                "task_key=excluded.task_key,revision=excluded.revision",
                (
                    root,
                    selected,
                    task_key if task_key is not None else (old["task_key"] if old else None),
                    revision if task_key is not None else (old["revision"] if old else revision),
                ),
            )
            if old is None or old["snapshot_id"] != selected:
                keep = tuple(
                    dict.fromkeys(
                        x for x in (current["current_id"], current["previous_id"], selected) if x
                    )
                )
                self._prune(db, root, keep)
            return selected

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=DELETE")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            allowed = self.max_bytes // page_size
            actual = db.execute(f"PRAGMA max_page_count={allowed}").fetchone()[0]
            if actual > allowed:
                raise ValueError("Existing database exceeds the configured storage budget")
            yield db
            db.commit()
        except sqlite3.Error as error:
            db.rollback()
            if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
                raise ValueError(
                    "Storage budget exceeded; the last valid record was preserved"
                ) from error
            raise
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _current(db: sqlite3.Connection, root: str) -> sqlite3.Row | None:
        return db.execute("SELECT * FROM checkouts WHERE root=?", (root,)).fetchone()

    @staticmethod
    def _require_current(db: sqlite3.Connection, root: str, snapshot_id: str) -> None:
        row = Store._current(db, root)
        if row is None or row["current_id"] != snapshot_id:
            raise ValueError(
                "Result belongs to a stale or unknown snapshot; scan and request it again"
            )

    @staticmethod
    def _annotation_rows(
        db: sqlite3.Connection, root: str, node_id: str | None = None
    ) -> dict[str, list[sqlite3.Row]]:
        result: dict[str, list[sqlite3.Row]] = {}
        for row in db.execute(
            "SELECT * FROM annotations WHERE root=?"
            + (" AND node_id=?" if node_id is not None else "")
            + " ORDER BY updated_at DESC",
            (root, node_id) if node_id is not None else (root,),
        ):
            result.setdefault(row["node_id"], []).append(row)
        return result

    @staticmethod
    def _annotation(row: sqlite3.Row, candidates: dict[str, list[sqlite3.Row]]) -> dict[str, Any]:
        data = json.loads(row["data"]) if isinstance(row["data"], str) else dict(row["data"])
        options = candidates.get(row["id"], [])
        match = next((x for x in options if x["fingerprint"] == row["fingerprint"]), None)
        chosen = match or (options[0] if options else None)
        if chosen is not None:
            data["annotation"] = json.loads(chosen["body"])
            data["annotation_status"] = "current" if match is not None else "stale"
            data["annotation_fingerprint"] = chosen["fingerprint"]
        return data

    def _node_rows(
        self,
        db: sqlite3.Connection,
        root: str,
        snapshot_id: str,
        fingerprints: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        row = db.execute(
            "SELECT data FROM node_sets WHERE root=? AND snapshot_id=?", (root, snapshot_id)
        ).fetchone()
        if row is None:
            raise ValueError("Saved graph is incomplete: its node set is missing")
        if fingerprints is None:
            metadata = db.execute(
                "SELECT metadata FROM snapshots WHERE root=? AND id=?", (root, snapshot_id)
            ).fetchone()
            fingerprints = json.loads(metadata["metadata"])["files"]
        return [
            node | {"fingerprint": fingerprints.get(node["path"], "")}
            for node in decode_nodes(row["data"], maximum=self.max_bytes)
        ]

    def _refresh_search(self, db: sqlite3.Connection, root: str) -> None:
        db.execute("DELETE FROM search WHERE root=?", (root,))
        current = self._current(db, root)
        if current is None:
            return
        candidates = self._annotation_rows(db, root)
        documents = []
        for row in self._node_rows(db, root, current["current_id"]):
            if row["kind"] == "external":
                continue
            data = self._annotation(row, candidates)
            overview = str(data.get("annotation", {}).get("overview", ""))
            documents.append(
                (
                    row["name"],
                    row["path"],
                    overview,
                    root,
                    row["id"],
                    data.get("annotation_status", "missing"),
                )
            )
        db.executemany("INSERT INTO search VALUES (?,?,?,?,?,?)", documents)

    @staticmethod
    def _prune(db: sqlite3.Connection, root: str, keep: tuple[str, ...]) -> None:
        placeholders = ",".join("?" for _ in keep)
        db.execute(
            f"DELETE FROM snapshots WHERE root=? AND id NOT IN ({placeholders})", (root, *keep)
        )
        # Keep matching annotations for the two snapshots, plus the latest stale
        # annotation for a surviving node until it has been refreshed.
        db.execute(
            """
            DELETE FROM annotations AS a WHERE root=? AND (
              NOT EXISTS(SELECT 1 FROM source_nodes n JOIN snapshots s ON s.record_id=n.snapshot_key
                         WHERE s.root=a.root AND n.id=a.node_id)
              OR (NOT EXISTS(SELECT 1 FROM source_nodes n JOIN snapshots s ON s.record_id=n.snapshot_key
                             WHERE s.root=a.root AND n.id=a.node_id AND n.fingerprint=a.fingerprint)
                  AND EXISTS(SELECT 1 FROM annotations newer WHERE newer.root=a.root
                             AND newer.node_id=a.node_id AND newer.updated_at>a.updated_at)))
            """,
            (root,),
        )

    def save_graph(self, graph: Graph) -> None:
        root = _root(graph.snapshot.root)
        if len({node.id for node in graph.nodes}) != len(graph.nodes):
            raise ValueError("Graph contains duplicate node identities")
        metadata = asdict(graph.snapshot) | {"root": root, "diagnostics": graph.diagnostics}
        observations = [
            asdict(node)
            | {
                "data": {
                    key: value
                    for key, value in node.data.items()
                    if not key.startswith("annotation")
                }
            }
            for node in graph.nodes
        ]
        node_blob, node_bytes = encode_nodes(observations)
        edge_blob, edge_bytes = encode_edges(graph.edges)
        if node_bytes + edge_bytes > self.max_bytes * 0.8:
            raise ValueError(
                "Graph exceeds its storage budget; the last valid record was preserved"
            )
        metadata["_graph_digest"] = sha256(
            node_blob + edge_blob + _encode(graph.diagnostics).encode()
        ).hexdigest()
        metadata["_node_counts"] = dict(Counter(node.kind for node in graph.nodes))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self._current(db, root)
            existing = db.execute(
                "SELECT metadata FROM snapshots WHERE root=? AND id=?", (root, graph.snapshot.id)
            ).fetchone()
            if existing is not None:
                saved = json.loads(existing["metadata"])
                if saved["files"] != graph.snapshot.files:
                    raise ValueError(
                        "Snapshot identity was reused for different source fingerprints"
                    )
                if (
                    old
                    and old["current_id"] == graph.snapshot.id
                    and saved.get("_graph_digest") == metadata["_graph_digest"]
                ):
                    return
            previous = (
                None
                if old is None
                else (
                    old["previous_id"]
                    if old["current_id"] == graph.snapshot.id
                    else old["current_id"]
                )
            )
            db.execute(
                "INSERT INTO snapshots(root,id,metadata) VALUES (?,?,?) ON CONFLICT(root,id) DO UPDATE SET metadata=excluded.metadata",
                (root, graph.snapshot.id, _encode(metadata)),
            )
            record_id = db.execute(
                "SELECT record_id FROM snapshots WHERE root=? AND id=?", (root, graph.snapshot.id)
            ).fetchone()[0]
            db.execute("DELETE FROM source_nodes WHERE snapshot_key=?", (record_id,))
            db.executemany(
                "INSERT INTO source_nodes VALUES (?,?,?)",
                (
                    (record_id, node.id, graph.snapshot.files[node.path])
                    for node in graph.nodes
                    if node.kind != "external" and node.path in graph.snapshot.files
                ),
            )
            db.execute(
                "INSERT INTO node_sets VALUES (?,?,?) ON CONFLICT(root,snapshot_id) "
                "DO UPDATE SET data=excluded.data",
                (root, graph.snapshot.id, node_blob),
            )
            db.execute(
                "INSERT INTO edge_sets VALUES (?,?,?,?) ON CONFLICT(root,snapshot_id) "
                "DO UPDATE SET data=excluded.data,edge_count=excluded.edge_count",
                (root, graph.snapshot.id, edge_blob, len(graph.edges)),
            )
            db.execute(
                "INSERT INTO checkouts VALUES (?,?,?) ON CONFLICT(root) DO UPDATE SET current_id=excluded.current_id,previous_id=excluded.previous_id",
                (root, graph.snapshot.id, previous),
            )
            baseline = db.execute(
                "SELECT snapshot_id FROM baselines WHERE root=?", (root,)
            ).fetchone()
            keep = tuple(
                dict.fromkeys(
                    x for x in (graph.snapshot.id, previous, baseline[0] if baseline else None) if x
                )
            )
            self._prune(db, root, keep)
            self._refresh_search(db, root)
            self._changed(db, root)

    def load_graph(self, root: str, snapshot_id: str | None = None) -> Graph | None:
        root = _root(root)
        with self._connection() as db:
            db.execute("BEGIN")
            current = self._current(db, root)
            if snapshot_id is None:
                if current is None:
                    return None
                snapshot_id = current["current_id"]
            row = db.execute(
                "SELECT metadata FROM snapshots WHERE root=? AND id=?", (root, snapshot_id)
            ).fetchone()
            if row is None:
                return None
            metadata = json.loads(row["metadata"])
            diagnostics = tuple(metadata.pop("diagnostics", ()))
            metadata.pop("_graph_digest", None)
            metadata.pop("_node_counts", None)
            candidates = self._annotation_rows(db, root)
            nodes = tuple(
                Node(
                    row["id"],
                    row["kind"],
                    row["name"],
                    row["path"],
                    row["line"],
                    self._annotation(row, candidates),
                )
                for row in self._node_rows(db, root, snapshot_id, metadata["files"])
            )
            edge_row = db.execute(
                "SELECT data FROM edge_sets WHERE root=? AND snapshot_id=?", (root, snapshot_id)
            ).fetchone()
            if edge_row is None:
                raise ValueError("Saved graph is incomplete: its edge set is missing")
            edges = decode_edges(edge_row["data"], maximum=self.max_bytes)
            return Graph(Snapshot(**metadata), nodes, edges, diagnostics)

    def annotate(
        self,
        root: str,
        snapshot_id: str,
        node_id: str,
        annotation: dict[str, Any],
        *,
        expected_digest: str | None = None,
    ) -> bool:
        root = _root(root)
        body = _encode(annotation, limit=65_536)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_current(db, root, snapshot_id)
            node = db.execute(
                "SELECT n.id,n.fingerprint,'{}' AS data FROM source_nodes n "
                "JOIN snapshots s ON s.record_id=n.snapshot_key WHERE s.root=? AND s.id=? AND n.id=?",
                (root, snapshot_id, node_id),
            ).fetchone()
            if node is None or not node["fingerprint"]:
                raise ValueError("Annotation target has no current source fingerprint")
            supplied = annotation.get("fingerprint", annotation.get("input_fingerprint"))
            if supplied is not None and supplied != node["fingerprint"]:
                raise ValueError("Annotation source fingerprint does not match its target")
            old = db.execute(
                "SELECT body FROM annotations WHERE root=? AND node_id=? AND fingerprint=?",
                (root, node_id, node["fingerprint"]),
            ).fetchone()
            if old is not None and old["body"] == body:
                return False
            if expected_digest is not None:
                current_annotation = self._annotation(
                    node, self._annotation_rows(db, root, node_id)
                ).get("annotation")
                actual = sha256(_encode(current_annotation).encode()).hexdigest()
                if actual != expected_digest:
                    raise ValueError("Annotation inputs changed; a newer result was already saved")
            db.execute(
                "INSERT INTO annotations VALUES (?,?,?,?,?) ON CONFLICT(root,node_id,fingerprint) DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at",
                (root, node_id, node["fingerprint"], body, time.time_ns()),
            )
            # Annotation changes one canonical document; rebuilding every source/symbol
            # document here made full mapping quadratic in repository size.
            db.execute(
                "UPDATE search SET overview=?,status='current' WHERE root=? AND node_id=?",
                (str(annotation.get("overview", "")), root, node_id),
            )
            self._changed(db, root)
        return True

    def search(self, root: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("Search limit must be between 1 and 100")
        # Quote terms rather than expose FTS query syntax as a second API.
        terms = [part for part in query.split() if part][:20]
        if not terms:
            return []
        expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        with self._connection() as db:
            rows = db.execute(
                "SELECT node_id AS id,name,path,overview,status FROM search WHERE search MATCH ? AND root=? ORDER BY rank LIMIT ?",
                (expression, _root(root), limit),
            )
            return [dict(row) for row in rows]

    def save_review(
        self, root: str, snapshot_id: str, review_id: str, kind: str, result: dict[str, Any]
    ) -> None:
        root = _root(root)
        if not review_id or not kind:
            raise ValueError("Review identity and kind are required")
        # Task/result sizing belongs to the analysis request. The store enforces
        # the complete database budget, not an unrelated 256 KiB review ceiling.
        body = _encode(result, limit=self.max_record_bytes)
        payload = zlib.compress(body.encode(), level=6)
        scope_key = _encode(
            {
                "scope": result.get("scope", review_id),
                "options": result.get("request_options", {}),
            }
        )
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_current(db, root, snapshot_id)
            old = db.execute(
                "SELECT snapshot_id,result FROM reviews WHERE root=? AND id=? AND kind=?",
                (root, review_id, kind),
            ).fetchone()
            if (
                old is not None
                and old["snapshot_id"] == snapshot_id
                and _review_text(old["result"], self.max_record_bytes) == body
            ):
                return
            db.execute(
                "INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(root,id,kind) DO UPDATE "
                "SET snapshot_id=excluded.snapshot_id,result=excluded.result,"
                "updated_at=excluded.updated_at,scope_key=excluded.scope_key",
                (root, review_id, kind, snapshot_id, payload, now, time.time_ns(), scope_key),
            )
            if kind == "request":
                db.execute(
                    "DELETE FROM reviews WHERE root=? AND kind='request' AND snapshot_id!=? "
                    "AND rowid NOT IN (SELECT rowid FROM reviews WHERE root=? AND kind='request' "
                    "AND snapshot_id!=? ORDER BY updated_at DESC LIMIT ?)",
                    (root, snapshot_id, root, snapshot_id, self.request_limit),
                )
            elif "scope" in result:
                # Keep every module/pattern shard, rather than only the last20
                # batches of a repository. Replace only the same logical analysis.
                db.execute(
                    "DELETE FROM reviews WHERE root=? AND kind=? AND scope_key=? AND id!=?",
                    (root, kind, scope_key, review_id),
                )
            if kind != "request":
                self._changed(db, "reviews:" + root)

    def get_review(
        self, root: str, review_id: str, kind: str | None = None
    ) -> dict[str, Any] | None:
        rows = self.reviews(root, kind, review_id=review_id)
        return rows[0] if rows else None

    def reviews(
        self,
        root: str,
        kind: str | None = None,
        *,
        review_id: str | None = None,
        include_requests: bool = True,
    ) -> list[dict[str, Any]]:
        root = _root(root)
        with self._connection() as db:
            db.execute("BEGIN")
            current = self._current(db, root)
            current_id = current["current_id"] if current else None
            statement = "SELECT * FROM reviews WHERE root=?"
            parameters = [root]
            if kind is not None:
                statement += " AND kind=?"
                parameters.append(kind)
            if review_id is not None:
                statement += " AND id=?"
                parameters.append(review_id)
            if not include_requests:
                statement += " AND kind!='request'"
            return [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "snapshot_id": row["snapshot_id"],
                    "result": json.loads(_review_text(row["result"], self.max_record_bytes)),
                    "created_at": row["created_at"],
                    "status": "current" if row["snapshot_id"] == current_id else "stale",
                }
                for row in db.execute(statement + " ORDER BY updated_at DESC", parameters)
            ]

    def stats(self, root: str) -> dict[str, Any]:
        root = _root(root)
        with self._connection() as db:
            db.execute("BEGIN")
            current = self._current(db, root)
            snapshot_id = current["current_id"] if current else None
            metadata = db.execute(
                "SELECT metadata FROM snapshots WHERE root=? AND id=?", (root, snapshot_id)
            ).fetchone()
            counts = json.loads(metadata["metadata"])["_node_counts"] if metadata else {}
            result: dict[str, Any] = {
                "root": root,
                "snapshot_id": snapshot_id,
                "previous_snapshot_id": current["previous_id"] if current else None,
                "pinned_snapshot_id": (
                    baseline[0]
                    if (
                        baseline := db.execute(
                            "SELECT snapshot_id FROM baselines WHERE root=?", (root,)
                        ).fetchone()
                    )
                    else None
                ),
                "nodes": sum(counts.values()),
                "modules": counts.get("module", 0),
                "node_kinds": counts,
                "max_bytes": self.max_bytes,
                "bytes": self.path.stat().st_size,
            }
            for table in ("snapshots", "reviews"):
                result[table] = db.execute(
                    f"SELECT count(*) FROM {table} WHERE root=?", (root,)
                ).fetchone()[0]
            edge_row = db.execute(
                "SELECT edge_count FROM edge_sets WHERE root=? AND snapshot_id=?",
                (root, snapshot_id),
            ).fetchone()
            result["edges"] = edge_row["edge_count"] if edge_row else 0
            candidates = self._annotation_rows(db, root)
            statuses = [
                self._annotation(row, candidates).get("annotation_status")
                for row in db.execute(
                    "SELECT n.id,n.fingerprint,'{}' AS data FROM source_nodes n "
                    "JOIN snapshots s ON s.record_id=n.snapshot_key WHERE s.root=? AND s.id=?",
                    (root, snapshot_id),
                )
            ]
            result["annotations"] = statuses.count("current")
            result["stale_annotations"] = statuses.count("stale")
            return result
