# Request 12 — Streaming Horizon Compaction & Cold-Slice Archival

Compact sealed bitemporal horizons older than a retention watermark into read-only summary checkpoints while preserving causal provenance hashes.

Old sealed periods are cold. Freeze them into summaries, but auditors and the replay tooling
must never notice a difference in what the ledger answers.

## What I need

`POST /api/compactions` with `{"watermark": W, "asserted_at": T}` → `200 {"compactions": [...]}`
compacts every seal on `main` (request 2) whose `effective_to < W` and that has not been
compacted yet, one summary per seal, sorted by seal id (an empty list when there is nothing to
do). It is a write (clock rule → `409 clock_regression`); integers ≥ 0 (`422 invalid_request`).

A summary:

```json
{"id": "cmp-q1", "seal_id": "q1", "effective_from": 0, "effective_to": 999,
 "asserted_at": 7000, "assertion_count": 12,
 "entries": [{"key": "acct:7:limit", "value": 500, "assertion_id": "a-3"}],
 "provenance_hash": "<64 hex>"}
```

- The **compacted assertions** are all assertions on `main` (retracted ones included) with
  `effective_from ≤ effective_at ≤ effective_to`, recorded before the compaction.
  `assertion_count` counts them.
- `entries`: for each key, the compacted assertion that would win at
  `(effective_to, T)` among the compacted assertions only (canonical order, retracted ones not
  visible), sorted by key.
- `provenance_hash`: SHA-256 of the canonical JSON of the list of
  `{"id", "causal_parents", "effective_at", "asserted_at", "retracted_at"}` of the compacted
  assertions (`retracted_at` as known at `T`), sorted by `id`.
- `GET /api/compactions` → `{"compactions": [...]}` sorted by id.

Compacted intervals are **read-only** from then on:

- a new assertion (direct or merged) with `effective_at` in a compacted interval, and a
  retraction, checkpoint or reconcile of a compacted assertion → `409 compacted` (this check
  comes before the seal check, so it wins even with a valid override);
- a compacted assertion counts as checkpointed at `T` (taint from retractions recorded after
  `T` stops there).

**Nothing observable changes otherwise.** Every read that worked before (records, listings,
state and grants at any coordinate, diffs, seal verification, bundles, clawbacks, …) gives the
same answer after compaction, including reads at coordinates older than `T`. Branches forked
before a compaction keep working, including merges whose assertions descend from compacted
ones. Summaries survive a restart.
