# Request 9 — Dead-Letter Quarantine & Byzantine Transducer Isolation

Automatically quarantine any transducer that emits 3 conservation-violating delta frames within 50 ticks and reroute its cells to fallback neighbors.

A faulty device kept spraying frames that create flux out of nothing. Rejecting them is not
enough; such a device has to be cut off, and its cells handed to healthy neighbours.

## What I need

### Violations

Every frame rejected with `conservation_violation` — from the frames endpoint or as a wire
frame — is a **violation**, recorded at the field's current `tick`, and the frame goes to the
field's **dead-letter** list. The responses for such frames do not change.

When a violation at tick `t` brings the transducer's violations with a tick in
`(t - 50, t]` to 3 or more, its quarantine becomes **pending**.

### Quarantine takes effect at the next tick boundary

A pending quarantine takes effect when the field next starts an Emit phase (sub-tick 0),
before any frame is applied — so if the field is at `sub_tick` 0 it happens at the next
micro-phase run, otherwise when the next tick starts. Until then the transducer works as
normal. At that moment, for all transducers whose quarantine takes effect together:

1. Their status becomes `quarantined` (permanently).
2. Their frames still waiting in the queue are removed and dead-lettered (reason
   `quarantined`); other transducers' frames keep their order.
3. Their cells are **rerouted**. Each cell (in canonical order) goes to the owner of the cell's
   first neighbour, in direction order 0–5, that is inside the field and owned by a transducer
   that is not quarantined, judging ownership as it was *before* this rerouting. If there is
   none the cell becomes unowned. Quarantined transducers end up with no cells; the cell lists
   of the new owners stay in canonical order.

A quarantined transducer's frames are refused: the frames endpoint answers
`409 quarantined`; wire frames that are not corrupt get a `rejected` receipt with error
`quarantined` (checked before anything else). Refused frames are not dead-lettered.

### API

- The transducer object gains `"status": "active" | "pending" | "quarantined"` and
  `"violations"` (violations ever recorded).
- `GET /api/fields/{id}/quarantine` → `{"quarantined": [ids], "pending": [ids]}`, ids sorted.
- `GET /api/fields/{id}/dead-letters` →
  `{"frames": [{"transducer": "t1", "reason": "conservation_violation", "tick": 12, "deltas": [{"q", "r", "delta"}, ...]}, ...]}`
  oldest first; `tick` is the field's tick when the frame was dead-lettered.
