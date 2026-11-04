# Request 0 — Design Bitemporal Causal Quarantine Engine (Seed Architecture)

Design and implement ChronoWeave: an in-memory and SQLite-backed bitemporal ledger where every assertion carries (effective_at, asserted_at, causal_parents). Support speculative branch forking, retroactive tombstone retraction, and deterministic state materialization at any (effective_t, assertion_t) coordinate.

This is the foundation everything else will build on, so take the design seriously. Many more
requests will follow.

## What I need

Every ledger operation is addressed to a **branch**. `main` always exists; other branches are
speculative copies of `main`. Branch names match `[a-z0-9-]{1,40}`.

### Assertions

`POST /api/branches/{branch}/assertions`

```json
{"id": "acct-7-limit-v2", "key": "acct:7:limit", "value": 500,
 "effective_at": 1000, "asserted_at": 1200,
 "causal_parents": ["acct-7-open"], "author": "alice", "node": "node-a"}
```

- `id`: matches `[A-Za-z0-9][A-Za-z0-9._:-]{0,63}`. Ids are unique within the branch's view:
  an id already visible on that branch (including one inherited from `main`) → `409 duplicate_assertion`.
- `key`: non-empty string, at most 200 characters. `value`: any JSON value (required; `null` allowed).
- `effective_at`, `asserted_at`: integers ≥ 0.
- `causal_parents`: list of distinct ids, default `[]`. Every parent must already be visible on
  the branch → otherwise `422 unknown_parent`. A parent that is retracted (on that branch's
  view) → `409 parent_retracted`.
- `author`: non-empty string. `node`: non-empty string, default `"local"`.
- Any other validation failure → `422 invalid_request`.
- **Transaction time never goes backwards.** Each branch has a clock: the largest
  `asserted_at` recorded on it so far (by any write). A write whose `asserted_at` is smaller
  than the clock → `409 clock_regression`. Equal is fine.
- `201` with the assertion record:

```json
{"id": "acct-7-limit-v2", "key": "acct:7:limit", "value": 500, "effective_at": 1000,
 "asserted_at": 1200, "causal_parents": ["acct-7-open"], "author": "alice", "node": "node-a",
 "retracted_at": null}
```

`GET /api/branches/{branch}/assertions/{id}` → the record, or `404 not_found`.

`GET /api/branches/{branch}/assertions?key=K&prefix=P` → `{"assertions": [record, ...]}`: every
assertion visible on the branch, retracted ones included, in **canonical order** (below).
`key` (exact match) and `prefix` (key starts with) are optional filters.

### Retraction

`POST /api/branches/main/assertions/{id}/retract` with `{"asserted_at": 1500, "author": "bob"}`
(`author` optional) → `200` with the record, now carrying `"retracted_at": 1500`. A retraction is
a tombstone: it is itself a write at `asserted_at`, and the assertion stops being visible to
coordinates whose `asserted_at` is at or after the retraction. Nothing is ever deleted.

- Unknown id → `404 not_found`; already retracted → `409 already_retracted`;
  clock rule as above → `409 clock_regression`.
- Retraction is only possible on `main`: on any other branch → `409 main_only`.

### Materialized state

`GET /api/branches/{branch}/state?effective_at=E&asserted_at=A&prefix=P` (all optional; an
omitted coordinate means "no bound"):

```json
{"branch": "main", "effective_at": 1000, "asserted_at": 1300,
 "entries": [{"key": "acct:7:limit", "value": 500, "assertion_id": "acct-7-limit-v2"}]}
```

An assertion is **visible at (E, A)** when `asserted_at ≤ A`, `effective_at ≤ E`, and it was
not retracted at or before `A`. For each key, the entry is the visible assertion that comes
**last in canonical order**. Keys with no visible assertion are absent. Entries are sorted by key.
Echo the coordinates as given (`null` when omitted).

**Canonical order** sorts assertions by `effective_at`, then `asserted_at`, then the order in
which this service recorded them (recorded earlier first). So when two assertions for one key
tie on both times, the one recorded later wins.

The same request with the same coordinate must always give the same answer, however much
is written afterwards, as long as later writes use later `asserted_at` values.

### Branches

`POST /api/branches` with `{"name": "what-if"}` → `201 {"name": "what-if", "status": "active"}`.
Existing name (or `main`) → `409 conflict`; bad name → `422 invalid_request`.

A new branch starts as a frozen copy of `main` exactly as it stands at that moment: it sees
every assertion and retraction recorded on `main` before the branch was created, and its clock
starts at `main`'s clock. Nothing written to `main` afterwards appears on the branch, and
nothing written to the branch appears on `main`. Unknown branch on any branch endpoint →
`404 not_found`.

`GET /api/branches` → `{"branches": [{"name": "main", "status": "active"}, ...]}` sorted by name.

### Persistence

Everything survives a restart. Keep it under `CHRONOWEAVE_DATA_DIR` (SQLite is fine).
