# Request 3 — Capability Dialect Negotiation Handshake

Allow transducers to advertise supported capability bitmasks and negotiate the highest common protocol dialect version before streaming field updates.

Our transducer fleet runs mixed firmware. Before a device streams, it should tell us what it
speaks and we agree on the best format we both know.

## What I need

### Dialects and versions

The JSON dialect (id `0`) now has three versions:

- `1.0`: `{"deltas": [{"q", "r", "delta"}, ...]}` — any other top-level key is an
  `invalid_payload`.
- `1.1`: like `1.0`, but the object may also carry `"note"` (a string, ignored).
- `2.0`: `{"d": [[q, r, delta], ...]}` — `d` is the only key; each entry is a 3-element array.

All versions still need a non-empty delta list with integer `q`/`r` and finite numbers.

`GET /api/dialects` →
`{"dialects": [{"name": "json", "id": 0, "versions": ["1.0", "1.1", "2.0"]}], "capabilities": 3}`
— dialects sorted by id, versions ascending; `capabilities` is the server's bitmask.

### Capabilities

| bit | value | name | meaning |
| --- | --- | --- | --- |
| 0 | 1 | `DELTA_FRAMES` | the transducer streams delta frames (required) |
| 1 | 2 | `SELECTIVE_NACK` | receipts and the window report `nacks` |

The server supports both (mask `3`).

### Handshake

`POST /api/fields/{id}/transducers/{tid}/handshake` with
`{"dialect": "json", "versions": ["1.0", "2.0"], "capabilities": 3}` →
`200 {"dialect": "json", "version": "2.0", "capabilities": 3}`.

- `versions` is a non-empty list of `"major.minor"` strings (each part an integer 0–255) and
  `capabilities` an integer 0–65535 → otherwise `422 invalid_request`. Unknown dialect name →
  `422 unsupported_dialect`.
- The agreed version is the **highest** version present in both the offer and the server's
  list for that dialect, comparing major then minor numerically. None in common →
  `409 no_common_version`.
- The agreed capabilities are `offer & server mask`; without `DELTA_FRAMES` →
  `409 no_common_capability`. On any error the previous agreement stays.
- A successful handshake replaces the previous agreement; it does not reset the window.
- Before any handshake a transducer has `json` `1.0` and capabilities `3`. The transducer
  object gains `"dialect": {"name": "json", "version": "1.0"}` and `"capabilities": 3`.

### Effect on wire frames

The dialect rule of request 2 (step 2 of processing) becomes:

- header dialect id unknown to the server → rejected `unsupported_dialect`;
- header dialect or version different from the transducer's agreement → rejected
  `dialect_mismatch`;
- the payload is decoded with the agreed version.

Without `SELECTIVE_NACK` agreed, `nacks` in receipts and in the window view is always `[]`
(the window itself works the same).
