# Request 10 — Portable Audit Bundle Export & Deterministic Replay Verification

Export a canonical, zstd-compressed JSON audit bundle of any bitemporal slice that another node can import and verify without trusting local indexes.

Auditors want a file they can take away and check on their own machine. (No zstd here: our
environment only has the standard library, so use gzip.)

## What I need

### Export

`GET /api/bundles/export?effective_from=F&effective_to=T&asserted_at=A` (all optional, no bound
when omitted) → `200`, `Content-Type: application/gzip`, body = gzip of the canonical JSON of:

```json
{"format": "chronoweave-bundle/1",
 "slice": {"effective_from": 0, "effective_to": 999, "asserted_at": 5000},
 "assertions": [{"id": "...", "key": "...", "value": ..., "effective_at": 10, "asserted_at": 20,
                 "causal_parents": [...], "author": "...", "node": "...", "retracted_at": null}],
 "digest": "<64 hex>"}
```

- `slice` echoes the parameters (`null` when omitted).
- `assertions`: every assertion on `main` asserted at or before `A` with `F ≤ effective_at ≤ T`,
  **plus every causal ancestor of those** (whatever their effective time), so the bundle is
  self-contained. Retracted ones are included; `retracted_at` is the retraction time if it is
  ≤ `A`, else `null`. Exactly those nine fields, in canonical order.
- `digest`: SHA-256 of the canonical JSON of the `assertions` list.
- gzip with modification time 0 (`gzip.compress(data, mtime=0)`), so exporting the same slice
  twice gives byte-identical files.

### Verify

`POST /api/bundles/verify` with the bundle bytes as the request body → `200
{"valid": true, "assertions": <count>, "digest": "<64 hex>"}`, checking only what is in the
file, never local data, in this order (the first failing check decides the error):

- not gzip, not JSON, wrong `format` or missing/ill-typed fields → `422 invalid_bundle`;
- recomputed digest differs from `digest` → `422 digest_mismatch`;
- an assertion whose parent is not in the bundle, a parent with a later `asserted_at` than its
  child, a `retracted_at` before its own `asserted_at`, or a duplicate id → `422 invalid_bundle`.

### Import

`POST /api/bundles/import` with the bundle bytes → same checks, then loads the assertions and
their retractions into `main` of the caller's tenant → `200 {"imported": <count>, "digest": "<64 hex>"}`.

- `main` must be empty (no assertions and no seals) → otherwise `409 ledger_not_empty`.
- Assertions keep their ids, times, parents, authors and nodes; `main`'s clock becomes the
  largest time in the bundle. Imports are not run through verifiers, and do not create
  obligations. Assertion budgets apply (`409 budget_exceeded`).
- After an import, every read of `main` at any coordinate within the slice answers exactly as it
  did on the exporting node.
