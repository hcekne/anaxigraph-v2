# Request 19 — Stop hammering broken providers

When a provider goes down we keep hammering it and our endpoints hang. Fix both.

## What I need

**Timeouts.** Give up on any provider request after 5 seconds → `504 upstream_timeout`
(audit `outcome: "error"`, `status: null`).

**Circuit breaking**, per workspace and integration. Setting `breaker`:
`{"threshold": 3, "cooldown_seconds": 30}` (`threshold` ≥ 1, `cooldown_seconds` > 0) or `null`.
Default `null` (off).

- A *failure* is an operation that ends in a provider `5xx`, a timeout, a connection error or
  exhausted rate-limit retries. Client errors (unknown city, bad credentials, validation, quota)
  are not failures and leave the count unchanged; successes reset it.
- After `threshold` consecutive failures the circuit opens: operations return
  `503 circuit_open` immediately, contact no provider and cost no quota.
- After `cooldown_seconds`, the next operation is let through: success closes the circuit,
  failure opens it for another cooldown.
- While open, the overview shows `"status": "degraded"` with a `detail`, without a live check.
  Health checks neither count as failures nor close the circuit.
