# Request 11 — Cross-Dialect Schema Bridge & Lossless Down-Sampling

Bridge high-frequency 64-bit float transducers to 16-bit quantized edge transducers while tracking quantization residual error in a compensation accumulator.

Some of our edge hardware can only actuate in fixed steps: it applies whole multiples of a
step size held in a 16-bit (or even 8-bit) register. Whatever such a device is asked to move
must be rounded to that grid — but rounding must never lose flux. The error is carried over to
the next frame instead.

## What I need

### Bridging a transducer

`PUT /api/fields/{id}/transducers/{tid}/bridge` with `{"bits": 16, "step": 0.0009765625}` →
`200` transducer object. `bits` is `8` or `16`; `step` is a finite number `> 0`
(`422 invalid_request` otherwise). It can be set again to change bits/step; accumulated
residuals are kept.

The transducer object gains `"bridge": {"bits": 16, "step": 0.0009765625}` (or `null`) and
`"residuals": [{"q": 0, "r": 0, "residual": 0.0234375}, ...]` — every cell for which this
transducer's accumulator is not `0.0`, in canonical order.

### Quantized application

Frames are validated and queued exactly as before (raw deltas must still sum to zero). When a
frame from a bridged transducer is **applied** (at Emit), each delta, in listed order, goes
through the transducer's compensation accumulator `res[c]` (one per cell, starting at `0.0`):

```
x       = delta + res[c]
n       = round(x / step)            # to the nearest integer, ties to even (Python's round)
n       = clamp(n, -2**(bits-1), 2**(bits-1) - 1)
applied = n * step
res[c]  = x - applied
v[c]    = v[c] + applied
```

The conservation ledger still adds the raw `delta` to `expected_total`. Accumulators belong to
the transducer: they stay with it even if its cells are later rerouted.

### Audit

The audit gains `"residual_total"`: all accumulators added from `0.0`, transducers in id order,
cells in canonical order. Drift now includes it:
`drift = ((cells_total + sink_total) + residual_total) - expected_total`, and `ok` is computed
as before. Residuals are part of a field's state: checkpoints, restore and restarts keep them.
