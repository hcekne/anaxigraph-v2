# Request 2 — Epoch Seal Witnesses (Immutable Horizon Locks)

Add cryptographic epoch seals that freeze effective_t intervals against retroactive assertions unless accompanied by an OverrideAuthority proof.

Quarter close: once finance seals a period, nobody may slip a change into it without the
authority signing off. (No asymmetric crypto library is available here; an HMAC with a
secret the authority holds is our proof.)

## What I need

**Authority secret.** `PUT /api/authority` with `{"secret": "<non-empty string>"}` → `204`.
Write-only; may be replaced.

**Seals.** `POST /api/branches/main/seals` with
`{"id": "q1", "effective_from": 0, "effective_to": 999, "asserted_at": 2000}` → `201`:

```json
{"id": "q1", "effective_from": 0, "effective_to": 999, "asserted_at": 2000,
 "digest": "<64 hex>"}
```

- `id` matches `[A-Za-z0-9][A-Za-z0-9._:-]{0,63}`, unique (`409 conflict`). Integers ≥ 0 with
  `effective_from ≤ effective_to` (else `422 invalid_request`). A seal is a write (clock rule →
  `409 clock_regression`). `main` only (`409 main_only`).
- `digest`: SHA-256 of the canonical JSON of the list of
  `{"id", "key", "value", "effective_at", "asserted_at"}` for every assertion on `main` with
  `effective_from ≤ effective_at ≤ effective_to` that is visible at the seal's `asserted_at`
  (not retracted at or before it), as recorded before the seal, sorted by `id`.
- `GET /api/branches/main/seals` → `{"seals": [...]}` sorted by `id`;
  `GET /api/branches/main/seals/{id}` → one or `404 not_found`.
- `GET /api/branches/main/seals/{id}/verify` → `{"id": "q1", "valid": true, "digest": "<recomputed>"}`:
  recompute the digest from the ledger's history and compare with the stored one.

**What a seal freezes.** Once a seal exists, on `main`:

- a new assertion whose `effective_at` lies in a sealed interval, and
- a retraction of an assertion whose `effective_at` lies in a sealed interval

are refused with `409 sealed_interval`, unless the body carries an override:

```json
"override": {"seal_id": "q1", "proof": "<hex>"}
```

`proof` = HMAC-SHA256 with the authority secret as key over the UTF-8 text
`"<action>:<seal_id>:<assertion_id>"`, where `action` is `assert` or `retract`. The named
seal must cover the `effective_at` in question; when several seals cover it, a valid proof for
any one of them is enough. A wrong proof, a seal that does not cover it, an unknown seal or no
authority secret configured → `403 invalid_override`. An override on a write that needs none
is ignored.

Checkpoints and reconciliations are not affected by seals. Branches are not affected either.
