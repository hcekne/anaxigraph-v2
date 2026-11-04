# Request 20 — Simultaneous Sector Partition + Quantized Bridge + Quarantine Failover

During a 3-sector halo exchange, quarantine a Byzantine quantized transducer mid-tick while hot-reloading a .loom policy and verify 1e-12 conservation.

Incident review: a bridged edge device went Byzantine right where three sectors meet, while an
operator was rolling out a new rule. We need to rehearse exactly that, repeatably, and the
books must balance at every micro-phase. Two changes and one new tool.

## What I need

### 1. Quarantining a bridged transducer flushes its residuals

When a quarantine takes effect, after its cells are rerouted, every non-zero compensation
residual of that transducer is **flushed** into the field: for each of its accumulator cells in
canonical order, `v[c] = v[c] + res[c]` on the first plane, then `res[c] = 0.0`. (Until now
residuals stayed with a quarantined transducer forever.) `residual_total` drops by exactly what
the cells gain, so the audit stays balanced. Telemetry deltas of that tick include the flushed
amounts (the "value just before that tick's Emit" is taken before any boundary transition).

The full order at a tick boundary is now: (1) a staged policy activates; (2) quarantines take
effect, transducers in id order — status, dead-lettering of queued frames, rerouting, flushing
residuals; (3) queued frames are applied.

### 2. Operators can quarantine a transducer

`POST /api/fields/{id}/transducers/{tid}/quarantine` → `202` transducer object. An `active`
transducer becomes `pending` exactly as if it had reached the violation limit (it takes effect
at the next tick boundary); for a `pending` or `quarantined` one nothing changes.

### 3. Scheduled faults

To rehearse incidents deterministically, actions can be scheduled at an exact micro-phase:

`POST /api/fields/{id}/faults` with `{"at": {"tick": 5, "sub_tick": 2}, "action": {...}}` →
`201 {"id": 1, "at": {"tick": 5, "sub_tick": 2}, "action": {...}, "status": "scheduled"}` (ids
count up from 1 per field). Actions:

- `{"type": "quarantine", "transducer": "t1"}` — as the operator endpoint above;
- `{"type": "stage_policy", "source": "...", "fuel": 64}` — as `POST .../policy/stage`;
- `{"type": "frame", "transducer": "t1", "deltas": [{"q", "r", "delta"}, ...]}` — as
  `POST .../frames` (a conservation-violating frame is a violation like any other; a frame
  that is refused is simply dropped).

A fault **fires** when stepping is about to run micro-phase `sub_tick` of tick `tick` — mid-tick
if `sub_tick` is not 0 — before that micro-phase runs; faults due at the same moment fire in id
order, and its status becomes `fired`. What a fault starts takes effect by the usual rules (a
quarantine or a staged policy at the next tick boundary), never in the middle of a tick.

Validation when scheduling: a position before the field's current `(tick, sub_tick)` →
`422 in_the_past`; unknown transducer → `404 not_found`; unknown action type or bad fields →
`422 invalid_request`; a rule that would not compile or verify → the stage endpoint's errors.
`GET /api/fields/{id}/faults` → `{"faults": [...]}` in id order. Faults are part of a field's
state. How you step (whole ticks, single micro-phases, workers, sector order) never changes when
a fault fires or what happens.

The conservation audit must be `ok` at every sub-tick throughout.
