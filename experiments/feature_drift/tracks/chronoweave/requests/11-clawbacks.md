# Request 11 — Retroactive Fee & Royalty Clawback Calculator

Compute net financial adjustments when a historical grant assertion is retracted 30 days later, accounting for tiered volume brackets.

We record royalty payouts as assertions. When one is taken back, finance needs to know who owes
what, and our fee is tiered by the payee's volume.

## What I need

A **grant** is an assertion on `main` whose key starts with `grant:` and whose value is
`{"payee": "<non-empty string>", "amount": "<decimal string>"}` with a positive amount of at
most 2 decimal places (other fields in the value are ignored).

**Fee schedule** (per tenant): `PUT /api/fees/schedule` with

```json
{"brackets": [{"up_to": "1000.00", "rate": "0.05"},
              {"up_to": "5000.00", "rate": "0.03"},
              {"up_to": null, "rate": "0.01"}]}
```

→ `200` echoing it. Brackets are marginal: the first `1000.00` of volume pays 5 %, the next
`4000.00` pays 3 %, everything above pays 1 %. `up_to` values are positive decimal strings,
strictly increasing; only the last bracket has `null`, and it must. `rate` is a decimal string
from 0 to 1. Anything else → `422 invalid_request`. `GET /api/fees/schedule` → the schedule or
`409 not_configured`.

**Clawback.** `GET /api/clawbacks/{assertion_id}` for a retracted grant:

```json
{"assertion_id": "g-9", "payee": "studio-a", "gross": "800.00",
 "volume_before": "5800.00", "volume_after": "5000.00",
 "fee_before": "178.00", "fee_after": "170.00", "marginal_fee": "8.00",
 "days_elapsed": 31, "late": true,
 "payee_clawback": "792.00", "fee_refund": "0.00", "net_adjustment": "792.00"}
```

- `volume_before`: the sum of `amount` over the payee's grants on `main` that were not
  retracted just before this retraction was recorded (this grant included, any effective time);
  `volume_after` = `volume_before - gross`.
- `fee(v)`: the marginal bracket formula, exact; `fee_before = fee(volume_before)` and
  `fee_after = fee(volume_after)`, each rounded half-up to 2 places;
  `marginal_fee = fee_before - fee_after`.
- `days_elapsed`: whole days from the grant's `asserted_at` to its retraction (rounded down).
  `late` is `true` when that span is 30 days (2 592 000) or more.
- `payee_clawback = gross - marginal_fee` (what the payee actually received for this grant).
- `fee_refund`: `marginal_fee`, or `"0.00"` when `late` (fees on closed periods are kept).
- `net_adjustment = payee_clawback + fee_refund` (what goes back to the payer).
- All money as strings with exactly 2 decimal places.

Errors: unknown id → `404 not_found`; not retracted → `409 not_retracted`; not a grant →
`422 not_a_grant`; no fee schedule → `409 not_configured`. Clawbacks are computed from
history, so asking again later gives the same answer.
