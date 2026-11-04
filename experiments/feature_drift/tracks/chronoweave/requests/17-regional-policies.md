# Request 17 — Five Regional Compliance Policy Plugins (EU, US-CA, JP, BR, SG)

Add five regional data-residency and right-to-erasure bitemporal policy plugins with zero changes to existing verifiers or core engine.

Legal signed off on five regions. Each becomes one more verifier a tenant can switch on; the
existing verifiers and the way the ledger works must not change.

## What I need

Personal data lives under keys `pii:<region>:<subject>:<field>`; an **erasure request** for a
subject is an assertion on `main` with key `erasure:<region>:<subject>` and value `true`.
Regions are `eu`, `us-ca`, `jp`, `br`, `sg`. Subjects and fields contain no `:`.

Five new verifiers, listed by `GET /api/verifiers` and configured with
`PUT /api/verifiers/{name}` like the existing ones (same pre-commit rules, same name ordering,
disabled by default). Config for all five: `{"allowed_nodes": [str] | null}` — the key is required; `null` = any node.
Each only looks at assertions whose key starts with `pii:<its region>:`.

| name | region | residency | erasure |
| --- | --- | --- | --- |
| `policy_eu` | `eu` | yes | full |
| `policy_us_ca` | `us-ca` | no | forward |
| `policy_jp` | `jp` | yes | forward |
| `policy_br` | `br` | yes | full |
| `policy_sg` | `sg` | no | full |

- **Residency** (only where "yes"): when `allowed_nodes` is not `null` and the assertion's `node`
  is not in it → `409 residency_violation`. Where "no", `allowed_nodes` is accepted but ignored.
- **Erasure**: let R be the erasure request for the same region and subject that is visible on
  `main` (not retracted) at the assertion's `asserted_at`; if there are several, the one with the
  smallest `effective_at`.
  - `full`: if R exists → `409 erasure_requested`.
  - `forward`: if R exists and the assertion's `effective_at ≥` R's `effective_at` →
    `409 erasure_requested` (back-dated corrections before the request are still allowed).
