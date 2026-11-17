"""The V2 schema: one record per file of each graph, plus small mutable indexes."""

SCHEMA_VERSION = 9

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    record_id INTEGER PRIMARY KEY,
    root TEXT NOT NULL, id TEXT NOT NULL, metadata TEXT NOT NULL,
    UNIQUE(root, id)
);
CREATE TABLE IF NOT EXISTS checkouts (
    root TEXT PRIMARY KEY, current_id TEXT NOT NULL, previous_id TEXT
);
CREATE TABLE IF NOT EXISTS baselines (
    root TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL,
    task_key TEXT, revision TEXT,
    FOREIGN KEY(root, snapshot_id) REFERENCES snapshots(root, id)
);
CREATE TABLE IF NOT EXISTS knowledge_versions (
    root TEXT PRIMARY KEY, version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS graph_parts (
    root TEXT NOT NULL, path TEXT NOT NULL, key TEXT NOT NULL,
    size INTEGER NOT NULL, data BLOB NOT NULL,
    PRIMARY KEY(root, path, key)
);
CREATE TABLE IF NOT EXISTS snapshot_parts (
    snapshot_key INTEGER NOT NULL, position INTEGER NOT NULL,
    path TEXT NOT NULL, key TEXT NOT NULL,
    PRIMARY KEY(snapshot_key, position),
    FOREIGN KEY(snapshot_key) REFERENCES snapshots(record_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS snapshot_part_keys ON snapshot_parts(path, key);
CREATE TABLE IF NOT EXISTS source_nodes (
    snapshot_key INTEGER NOT NULL, id TEXT NOT NULL, fingerprint TEXT NOT NULL,
    PRIMARY KEY(snapshot_key, id),
    FOREIGN KEY(snapshot_key) REFERENCES snapshots(record_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS annotations (
    root TEXT NOT NULL, node_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
    body TEXT NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY(root, node_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS reviews (
    root TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL,
    snapshot_id TEXT NOT NULL, result TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at INTEGER NOT NULL, scope_key TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(root, id, kind)
);
CREATE INDEX IF NOT EXISTS review_kind ON reviews(root, kind, updated_at);
CREATE INDEX IF NOT EXISTS review_scope ON reviews(root,kind,scope_key);
DROP TABLE IF EXISTS search;
"""
