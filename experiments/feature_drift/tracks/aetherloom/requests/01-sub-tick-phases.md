# Request 1 — Sub-Tick Phase Ordering & Deterministic Flux Conservation

Divide each tick into 4 deterministic micro-phases (Emit, Propagate, Arbitrate, Commit) and verify that total scalar flux across all cells plus boundary sinks is conserved to 1e-12.

Our customers replay runs on different machines and compare them number by number, so "close
enough" is no longer enough: same inputs, same bits.

## What I need

### Micro-phases

Every tick is four micro-phases, numbered by `sub_tick`:

| sub_tick | phase | effect |
| --- | --- | --- |
| 0 | `emit` | queued transducer frames are applied (as in request 0) |
| 1 | `propagate` | every flow of the diffusion rule is computed from the cell values at this moment and staged; cells do not change |
| 2 | `arbitrate` | staged flows are settled; for now they pass through unchanged |
| 3 | `commit` | staged flows are applied to the cells and the sink; then `tick` increases by 1 and `sub_tick` returns to 0 |

- The field summary gains `"sub_tick"` (the next micro-phase to run, 0–3) and `"phase"` (its
  name from the table).
- `POST /api/fields/{id}/step` accepts either `{"ticks": n}` (1–1000, runs `4n` micro-phases)
  or `{"sub_ticks": n}` (1–4000, runs `n` micro-phases). Both together → `422 invalid_request`;
  neither → one tick. Starting mid-tick is fine: `{"ticks": 1}` at `sub_tick` 2 ends at the
  next tick's `sub_tick` 2.
- A frame accepted while a tick is in progress (`sub_tick` ≠ 0) waits for the next tick's
  Emit.
- `PUT /api/fields/{id}/cells` while a tick is in progress → `409 mid_tick` (nothing changes).

### Bit-identical diffusion

Results must be bit-identical to the following, evaluated in IEEE-754 double precision in
exactly this order — no reassociation, no fused multiply-add, no summing flows before
subtracting them. `v` are the values when Propagate ran:

```
for each cell c:
    acc = v[c]
    for d in 0..5:                         # direction order from request 0
        n = neighbour of c in direction d
        if n is in the field: acc = acc - (k * (v[c] - v[n]))
        else:                 acc = acc - (k * v[c])
    new[c] = acc
sink: for each cell c in canonical order, for d in 0..5 where the neighbour is missing:
    sink = sink + (k * v[c])
```

Frame deltas are applied as `v[c] = v[c] + delta` in acceptance order, then listed order.
Summary `total` keeps adding cell values in canonical order starting from `0.0`.

### Conservation audit

`GET /api/fields/{id}/audit` →

```json
{"tick": 4, "sub_tick": 0, "cells_total": 31.5, "sink_total": 8.5,
 "expected_total": 40.0, "drift": 0.0, "ok": true}
```

- `cells_total` is the summary `total`; `sink_total` the summary `sink`.
- `expected_total` is everything put into the field from outside, accumulated in the order it
  happened starting from `0.0`: each cell set by `PUT .../cells` adds `new - old`, each
  applied frame delta adds `delta`.
- `drift = (cells_total + sink_total) - expected_total`;
  `ok` is `|drift| <= 1e-12 * max(1, |expected_total|)`.
- The audit is available at every sub-tick, and must be `ok` at every sub-tick of every
  field.
