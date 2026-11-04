# Request 15 — Spatial Partitioning into Autonomous Sub-Grids (Sector Sharding)

Partition large hex grids into autonomous 16x16 sectors that step in parallel and exchange ghost-halo boundary rings at the Propagate phase.

Big fields are slow and our servers have cores to spare. Split fields into sectors that can be
worked on independently and in parallel — without changing a single bit of the results — and
show operators how much flux crosses between sectors.

## What I need

### Sectors

- `POST /api/fields` accepts `"sector_size": S` (integer 2–64, default 16); the summary gains
  `"sector_size"`.
- The cell `(q, r)` belongs to sector `(floor(q / S), floor(r / S))` (floor division, so
  `(-1, 0)` is in sector `(-1, 0)` for any `S`). Only sectors that contain cells exist.
- A sector's **halo** (ghost ring) is the set of cells of the field that are not in the sector
  but neighbour at least one of its cells.
- `GET /api/fields/{id}/sectors` →

  ```json
  {"sector_size": 16, "sectors": [{"sq": -2, "sr": 0, "cells": 120, "halo": 27, "total": 3.5, "exchange": -0.0125}, ...]}
  ```

  sorted by `(sq, sr)`. `cells` and `halo` are counts (a halo cell counts once even if it
  touches the sector several times or belongs to a corner shared by three sectors). `total` adds
  the sector's cells in canonical order, each cell's planes in plane order, from `0.0`.
  `exchange` is the net amount that moved into the sector from other sectors during the last
  completed Commit, all planes together: every flow whose source cell is outside the sector and
  whose receiving cell is inside counts positive, the reverse negative (edge flows as
  arbitrated; reflected flows count between the reflecting cell and the cell receiving them).
  It is `0.0` until the first Commit. The exchanges of all sectors add up to zero (up to
  rounding).

### Parallel stepping

`POST /api/fields/{id}/step` accepts two optional parameters:

- `"workers"`: integer 1–8 (default 1) — how many threads may work on sectors at once.
- `"sector_order"`: a list of `[sq, sr]` pairs naming every existing sector exactly once — the
  order in which sectors are handed out. Anything else → `422 invalid_request`.

Within Propagate, Arbitrate and Commit each sector computes its own cells, reading values of
its halo from the snapshot. Whatever `workers` and `sector_order` are, the results must be
**bit-identical** to the single-threaded rule (each cell's own operation order is fixed;
field-wide sums such as the sink keep canonical order).
