# Request 16 — Backpressure & Adaptive Tick Rate Throttling

When downstream telemetry consumers lag by >3 windows, coalesce intermediate sub-tick frames into conservative summary deltas without halting the field kernel.

Dashboards and control systems want a feed of what changed in a field, tick by tick. Some of
them fall behind for minutes. The engine must never wait for them, and their backlog must not
grow without bound — but nothing they would have seen may be lost or reordered.

## What I need

### Consumers and telemetry frames

- `POST /api/fields/{id}/consumers` with
  `{"id": "dash", "plane": "heat", "cells": [{"q": 0, "r": 0}, ...]}` → `201 {"id": "dash", "plane": "heat", "cells": 2, "lag": 0}`.
  `plane` defaults to the first plane (unknown → `422 invalid_request`); `cells` defaults to
  every cell; if given it must be a non-empty list without duplicates (`422 invalid_request`)
  of cells inside the field (`422 out_of_grid`).
  Existing id → `409 conflict`. `GET /api/fields/{id}/consumers` →
  `{"consumers": [...]}` (same objects, sorted by id);
  `DELETE /api/fields/{id}/consumers/{cid}` → `204`; unknown consumer → `404 not_found`.
- Every completed Commit appends one **telemetry frame** to every consumer of the field:

  ```json
  {"from_tick": 7, "to_tick": 7, "coalesced": 1,
   "deltas": [{"q": 0, "r": 0, "delta": -0.125}, ...],
   "events": [{"type": "policy_activated", "source": "if(v > 2, 0.5, 1)"}, {"type": "quarantined", "transducer": "t9"}]}
  ```

  `from_tick`/`to_tick` are the tick that just completed. `deltas` has every subscribed cell in
  canonical order with `delta = (value after the Commit) - (value just before that tick's
  Emit)`, on the consumer's plane. `events` lists the transitions that happened at that tick's
  boundary, in the order they happened: a staged policy activating, then each transducer
  quarantined (in id order); usually it is empty.
- `GET /api/fields/{id}/consumers/{cid}/frames` → `{"lag": n, "frames": [...]}`: the
  unacknowledged frames, oldest first (reading does not acknowledge).
- `POST /api/fields/{id}/consumers/{cid}/ack` with `{"through_tick": t}` drops every frame
  whose `to_tick <= t` → `200 {"lag": n}`. `lag` is the number of unacknowledged frames.

### Backpressure

A **window** is 8 frames. Whenever appending a frame makes a consumer's lag exceed 3 windows
(more than 24 frames), its backlog is **coalesced**: every maximal run of consecutive frames
without events is replaced by one summary frame (a run of one frame stays as it is). A
summary has the run's first `from_tick`, last `to_tick`, the sum of the `coalesced` counts, and
per cell the run's deltas added in order (starting from the first frame's delta). Frames with
events are never merged with anything. Stepping is never blocked or slowed by consumers.

Consumers are part of the field (they survive restarts) but are not rewound by a restore.
