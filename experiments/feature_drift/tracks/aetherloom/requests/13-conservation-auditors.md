# Request 13 — Six Domain Conservation Plugins (Mass, Momentum, Charge, EntropyCap, TokenQuota,HeatSink)

Add six pluggable conservation/invariant auditors that can be enabled per field plane.

Each customer cares about different invariants on different planes: total mass on one, charge
neutrality on another, a cap on how much a cell may hold on a third. They want to switch
checks on per plane and see them in the audit report. Expect more auditors later.

## What I need

### Endpoints

- `GET /api/auditors` → `{"auditors": [{"name": "charge"}, ...]}`, sorted by name (you may add
  a description of the parameters).
- `PUT /api/fields/{id}/planes/{plane}/auditors` with
  `{"auditors": [{"name": "mass", "params": {"expected": 10.0}}, {"name": "heat_sink", "params": {"max": 2.5}}]}`
  replaces that plane's auditors (an empty list clears them) →
  `200 {"plane": "<plane>", "auditors": [{"name": ..., "params": {...}}, ...]}` with defaults
  filled in. Unknown field or plane → `404 not_found`; unknown auditor → `422 unknown_auditor`;
  the same auditor twice, a missing required parameter, an unknown parameter or a bad value →
  `422 invalid_request`. `GET` on the same path returns the current configuration.
- The audit report (`GET /api/fields/{id}/audit`) gains
  `"auditors": [{"plane": "<plane>", "name": "mass", "value": 9.9999, "ok": true}, ...]`
  (planes in plane order, each plane's auditors in configured order) and `"auditors_ok"`: all
  of them ok (`true` when there are none). The existing conservation fields are unchanged.

### The auditors

For the plane being audited, `v` are its cell values. Sums start at `0.0` and go in canonical
order. Values need only be accurate to `1e-9` (relative), but the `ok` decision must follow the
rule. All parameters are numbers; `tolerance`, `max`, `max_imbalance` must be `>= 0`.

| name | params (default) | value | ok when |
| --- | --- | --- | --- |
| `mass` | `expected` (required), `tolerance` (`1e-9`) | `Σv + plane sink + residuals` (residuals: the audit's `residual_total` for the first plane, `0` for others) | `|value - expected| <= tolerance` |
| `momentum` | `max` (`1e-9`) | `max(|Σ q·v|, |Σ r·v|)` | `value <= max` |
| `charge` | `max_imbalance` (`1e-9`) | `Σv` | `|value| <= max_imbalance` |
| `entropy_cap` | `max` (required) | `-Σ p·ln(p)` over cells with `v > 0`, `p = v / S`, `S` the sum of those `v`; `0` if there are none | `value <= max` |
| `token_quota` | `max_cell` (required) | the largest `v` | `value <= max_cell` |
| `heat_sink` | `max` (required) | the plane's sink | `value <= max` |

Auditor configuration is part of the field (it survives restarts).
