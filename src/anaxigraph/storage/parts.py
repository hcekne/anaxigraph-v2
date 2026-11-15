"""Graph parts read outside the owning store: another checkout's, and the earlier layout."""

from __future__ import annotations

import sqlite3
from hashlib import sha256
from pathlib import Path

from anaxigraph.contracts import FilePart
from anaxigraph.storage.graph_codec import decode_part, decode_sets, encode_part
from anaxigraph.storage.schema import SCHEMA_VERSION

_CURRENT = (
    "SELECT g.path, g.key, g.data FROM checkouts c "
    "JOIN snapshots s ON s.root=c.root AND s.id=c.current_id "
    "JOIN snapshot_parts p ON p.snapshot_key=s.record_id "
    "JOIN graph_parts g ON g.root=c.root AND g.path=p.path AND g.key=p.key "
    "WHERE c.root=? ORDER BY p.position"
)


def read_parts(database: Path, root: Path, maximum: int) -> tuple[FilePart, ...] | None:
    """Another checkout's current parts, read without writing to or migrating its store."""
    try:
        db = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=2)
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                return None
            rows = db.execute(_CURRENT, (str(root.resolve()),)).fetchall()
        finally:
            db.close()
        return tuple(
            FilePart(path, key, *decode_part(data, maximum=maximum)) for path, key, data in rows
        )
    except (sqlite3.Error, ValueError, OSError):
        return None


def convert_sets(db: sqlite3.Connection, maximum: int) -> None:
    """Carry graphs saved as whole node and edge sets (schema 7 and earlier) into one part each."""
    db.execute("BEGIN IMMEDIATE")
    rows = db.execute(
        "SELECT s.record_id, n.root, n.data, e.data FROM node_sets n JOIN edge_sets e "
        "ON e.root=n.root AND e.snapshot_id=n.snapshot_id JOIN snapshots s "
        "ON s.root=n.root AND s.id=n.snapshot_id"
    ).fetchall()
    for record_id, root, node_set, edge_set in rows:
        blob = encode_part(*decode_sets(node_set, edge_set, maximum=maximum), "")
        key = sha256(blob).hexdigest()[:32]
        db.execute(
            "INSERT OR IGNORE INTO graph_parts VALUES (?,?,?,?,?)", (root, "", key, len(blob), blob)
        )
        db.execute("INSERT OR REPLACE INTO snapshot_parts VALUES (?,0,'',?)", (record_id, key))
    db.execute("DROP TABLE node_sets")
    db.execute("DROP TABLE edge_sets")
    db.commit()
