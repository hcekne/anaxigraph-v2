# Request 9 — Four Additional Domain Verifiers (Escrow, Compliance, GeoFence, RateCap)

Add four independent assertion pre-commit verifiers: EscrowHoldVerifier, GDPRRetentionVerifier, GeoFenceJurisdictionVerifier, and BurstRateCapVerifier.

Different tenants need different guard rails before anything lands in `main`. More verifiers
will come; each tenant switches on the ones it wants.

## What I need

`GET /api/verifiers` → `{"verifiers": [{"name": "burst_rate_cap", "enabled": false, "config": null}, ...]}`
sorted by name. `PUT /api/verifiers/{name}` with `{"enabled": true, "config": {...}}` → `200`
with the same shape for that verifier. `config` is validated by the verifier (bad config →
`422 invalid_request`; it may be omitted only when disabling). Unknown name → `404 not_found`.
Verifier settings are per tenant and survive a restart; every verifier starts disabled.

Every enabled verifier checks **every assertion about to be written to `main`**, directly or
by a merge (seen with the `asserted_at` it will get on `main`). Retractions, checkpoints and
writes to other branches are not verified. Enabled verifiers run in name order; the first
rejection wins and nothing is written (a merge is all-or-nothing). Rejections are `409` with
the verifier's code:

| name | config | rejects with | when |
| --- | --- | --- | --- |
| `escrow_hold` | `{"holds": [{"key": str, "until": int}]}` | `escrow_hold` | the assertion's `key` equals a hold's `key` and its `effective_at < until` |
| `gdpr_retention` | `{"prefixes": [str], "max_age_days": int ≥ 1}` | `retention_expired` | `key` starts with one of the prefixes and `asserted_at - effective_at > max_age_days × 86400` |
| `geofence_jurisdiction` | `{"allowed": [str], "nodes": {node: jurisdiction}}` | `jurisdiction_denied` | the jurisdiction of the assertion's `node` (from `nodes`) is missing or not in `allowed` |
| `burst_rate_cap` | `{"max": int ≥ 1, "window": int ≥ 1}` | `rate_cap_exceeded` | `main` already holds `max` or more assertions by the same `author` with `asserted_at` in `(asserted_at - window, asserted_at]`, counting retracted ones and those written earlier in the same merge |

Changing one verifier's settings never changes another's.
