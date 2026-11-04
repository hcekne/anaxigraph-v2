# Request 0 — Design Deterministic Sub-Tick Hexagonal Field & Protocol Synthesizer

Design and implement AetherLoom: a deterministic discrete spatial field engine on axial hex coordinates where protocol transducers exchange framed telemetry packets and enforce flux conservation across cell boundaries at sub-tick resolution.

This is the foundation everything else will build on, so take the time to get the core right.
For this first request I need fields, cells, ticking, transducers and their delta frames.

## What I need

### Hex grid

Cells use axial coordinates `(q, r)`. A field of radius `R` contains exactly the cells with
`max(|q|, |r|, |q + r|) <= R` (so `3R(R+1)+1` cells). The six neighbour directions, in this
fixed order (direction 0 to 5), are:

`(+1, 0)`, `(+1, -1)`, `(0, -1)`, `(-1, 0)`, `(-1, +1)`, `(0, +1)`

The neighbour of `(q, r)` in direction `d` is `(q + dq, r + dr)`. **Canonical cell order** is
ascending `q`, then ascending `r`. Every list of cells the API returns uses canonical order.

### Fields

- `POST /api/fields` with `{"id": "f1", "radius": 3, "k": 0.1}` → `201` with the field summary.
  `radius` is an integer 0–32; `k` (the diffusion coefficient) is a number with
  `0 < k <= 1/6`. Bad values → `422 invalid_request`; an existing id → `409 conflict`.
- Field summary: `{"id": "f1", "radius": 3, "k": 0.1, "tick": 0, "cells": 37, "total": 0.0, "sink": 0.0}`.
  `cells` is the cell count, `total` the sum of all cell values (added in canonical order
  starting from `0.0`), `sink` the amount absorbed by the boundary so far.
- `GET /api/fields` → `{"fields": [summary, ...]}` sorted by id; `GET /api/fields/{id}` →
  summary; unknown field → `404 not_found` (on every field endpoint below).
- Every cell starts at `0.0`. `PUT /api/fields/{id}/cells` with
  `{"cells": [{"q": 0, "r": 0, "value": 12.5}, ...]}` sets those cells' values → `200` summary.
  Values must be finite numbers (`422 invalid_request`); a coordinate outside the field →
  `422 out_of_grid` and nothing is changed.
- `GET /api/fields/{id}/cells` → `{"tick": 0, "cells": [{"q": -3, "r": 0, "value": 0.0}, ...]}`
  with every cell of the field in canonical order.

### Ticking

`POST /api/fields/{id}/step` with `{"ticks": n}` (integer 1–1000, default 1) advances the field
`n` ticks and returns the summary. One tick does two things, in this order:

1. **Frames**: every queued transducer frame is applied (see below).
2. **Diffusion**: let `v` be the cell values after step 1. Every cell's new value is computed
   from `v` only (never from values already updated in this tick):

   ```
   acc = v[c]
   for d in 0..5:
       n = neighbour of c in direction d
       if n is in the field: acc = acc - k * (v[c] - v[n])
       else:                 acc = acc - k * v[c]        # flows into the sink
   new[c] = acc
   ```

   The amount `k * v[c]` for each missing neighbour is added to the field's `sink`. The
   boundary is **absorbing**: what goes into the sink never comes back.

Results must be deterministic. For this request a value within `1e-9` of this rule is
acceptable; do follow the rule as written.

### Transducers and frames

- `POST /api/fields/{id}/transducers` with `{"id": "t1", "cells": [{"q": 0, "r": 0}, ...]}` →
  `201 {"id": "t1", "cells": [...]}` (cells in canonical order). The cell list must be
  non-empty without duplicates (`422 invalid_request`), inside the field (`422 out_of_grid`),
  and a cell belongs to at most one transducer (`409 cell_owned`). Existing id → `409 conflict`.
- `GET /api/fields/{id}/transducers` → `{"transducers": [...]}` sorted by id;
  `GET /api/fields/{id}/transducers/{tid}` → one, or `404 not_found`.
- `POST /api/fields/{id}/transducers/{tid}/frames` with
  `{"deltas": [{"q": 0, "r": 0, "delta": 0.5}, {"q": 1, "r": 0, "delta": -0.5}]}` queues a
  **delta frame** → `202 {"queued": <frames now waiting in this field>}`.
  - `deltas` is a non-empty list; each `delta` is a finite number (`422 invalid_request`).
  - Every cell must belong to this transducer → otherwise `422 foreign_cell`.
  - A frame only moves flux between cells; it may not create or destroy it. If the sum of
    its deltas (added in the listed order starting from `0.0`) has an absolute value above
    `1e-12` → `422 conservation_violation`. Rejected frames are not queued.
- At the start of the next tick, queued frames are applied in the order they were accepted,
  each delta in its listed order: `v[c] = v[c] + delta`. Then the queue is empty.
