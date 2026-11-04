# Request 16 — Circuit Breaker & Degraded Read-Only Mode for External Authority Oracles

Wrap remote authority witness verification in a circuit breaker that falls back to cached quorum certificates during network partitions.

Big customers no longer hand us an authority secret: their override authority is a remote
quorum service. It goes down now and then. When it does, we must not hang, we must not hammer
it, and one tenant's trouble must never affect another tenant.

## What I need

### Witness overrides

Wherever an override is accepted (request 2), it may instead be
`"override": {"seal_id": "q1", "witness": "<non-empty string>"}`. The seal must still cover the
`effective_at` in question (else `403 invalid_override`). The service asks the authority:

`POST {EXTERNAL}/authority/v1/tenants/{tenant}/certify` with JSON
`{"seal_id": "q1", "witness": "...", "action": "assert" | "retract", "assertion_id": "..."}`:

- `200 {"approved": true, "certificate": {"seal_id": "q1", "witness": "...", "quorum": ["n1", "n2", "n3"], "expires_at": 90000}}`
  → the write is authorized. Remember the certificate (per tenant, by seal id and witness).
- `200 {"approved": false}` → `403 invalid_override`.
- Any other status, an unusable body, a connection error, or no answer within 2 seconds is a
  **failure**. On a failure, a remembered certificate for the same seal and witness whose
  `expires_at ≥` the write's `asserted_at` authorizes the write. Without one →
  `503 authority_unavailable`, and nothing is written.

Certificates survive a restart.

### Circuit breaker (per tenant)

`PUT /api/authority/breaker` with `{"threshold": 3, "cooldown_seconds": 30}` (`threshold`
integer ≥ 1, `cooldown_seconds` number > 0) → `200` echoing it. That is also the default.

- Count consecutive failures; any non-failure answer (approval or denial) resets the count.
- After `threshold` consecutive failures the breaker **opens**: for `cooldown_seconds` the
  authority is not contacted at all; witness overrides are decided by remembered certificates
  alone (`503 authority_unavailable` without one).
- `cooldown_seconds` is wall-clock time. Once it has passed the breaker reports `half_open` until
  the next witness check, which is let through: a non-failure closes the breaker;
  a failure opens it for another cooldown.
- `GET /api/authority/status` →
  `{"breaker": "closed" | "open" | "half_open", "consecutive_failures": n, "degraded": bool, "cached_certificates": n}`.
  `degraded` is `true` while the breaker is open (half-open counts as open until a check
  goes through).

While a tenant is degraded every read keeps working, and so does every write that needs no
witness. Breaker state, counts and caches never cross tenants.
