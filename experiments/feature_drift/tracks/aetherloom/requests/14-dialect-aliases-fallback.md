# Request 14 — Dynamic getattr Dialect Dispatch & Metaclass Registry

Refactor dialect resolution to support runtime alias registration and dynamic fallback chains when a frame header specifies an unknown minor version.

Two field realities. Vendors call our dialects by their own names, and we want to register
those names at run time without a release. And device firmware gets ahead of us: a device
sends minor version 1.7 that we have never heard of, when everything it sends is still
readable as the 1.x we know.

## What I need

### Aliases

- `POST /api/dialects/aliases` with `{"alias": "cbor", "target": "cborlite"}` →
  `201 {"alias": "cbor", "target": "cborlite", "resolves_to": "cborlite"}`.
  - `alias` matches `[a-z][a-z0-9_-]{0,31}` (`422 invalid_request`); it may not equal a
    dialect name or an existing alias (`409 conflict`).
  - `target` is a dialect name **or another alias** (chains are allowed); anything else →
    `422 unsupported_dialect`.
- `PUT /api/dialects/aliases/{alias}` with `{"target": "..."}` re-points an existing alias
  (`404 not_found` if it doesn't exist). A change that would make a chain loop →
  `422 alias_cycle` (nothing changes).
- `DELETE /api/dialects/aliases/{alias}` → `204`; unknown → `404 not_found`; another alias
  targets it → `409 alias_in_use`.
- `GET /api/dialects/aliases` → `{"aliases": [{"alias", "target", "resolves_to"}, ...]}`
  sorted by alias; `GET /api/dialects` gains `"aliases": {"<alias>": "<resolves_to>", ...}`.
- Wherever a dialect is named — the handshake's `dialect`, and `{name}` in the encode/decode
  endpoints — an alias resolves through its whole chain to a real dialect. Responses and the
  transducer's agreement always show the real dialect name.
- Aliases are global (shared by all fields) and survive restarts.

### Minor-version fallback

When a version `M.n` must be resolved for a known dialect — in a wire frame header, or the
`version` of the encode/decode endpoints — and the dialect does not have `M.n`, try `M.(n-1)`,
`M.(n-2)`, … `M.0` and use the first version the dialect has. If none exists the wire frame is
rejected with `unsupported_version` (the codec endpoints answer `422 unsupported_version`).
The handshake still only agrees on versions both sides list exactly.

For wire frames, the resolved version must then equal the transducer's agreed version
(otherwise `dialect_mismatch`, as before), and the payload is decoded with it. So with `json`
`1.1` agreed, a frame whose header says `1.7` is decoded as `1.1`; one that says `2.5`
resolves to `2.0` and is a `dialect_mismatch`; one that says `3.1` is `unsupported_version`.
