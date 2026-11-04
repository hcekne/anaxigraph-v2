# Request 8 — Multi-Layer Field Coupling (Thermal, Pressure, Electromagnetic Planes)

Extend the spatial field from one scalar value per hex cell to N coupled physical planes with configurable cross-plane transfer matrices.

Our first industrial customer models heat, pressure and charge in the same vessel, and they
leak into each other. Every cell now holds one value per plane.

## What I need

### Creating multi-plane fields

`POST /api/fields` accepts, instead of `k`:

```json
{"id": "vessel", "radius": 4,
 "planes": [{"name": "thermal", "k": 0.1}, {"name": "pressure", "k": 0.05}, {"name": "em", "k": 0.15}],
 "coupling": [[0, 0.02, 0], [0.01, 0, 0.03], [0, 0, 0]]}
```

- 1–8 planes; names match `[a-z][a-z0-9_]{0,15}` and are unique; each `k` as before.
- `coupling` is optional (default all zeros): an N×N matrix of finite numbers `>= 0` with a
  zero diagonal and every row summing (in order, from `0.0`) to at most `1`. `C[i][j]` is the
  fraction of plane `i`'s value in a cell that moves to plane `j` of the same cell each tick.
- Give exactly one of `k` and `planes` (`422 invalid_request` otherwise; also for any invalid
  plane or matrix). A field created with `k` has a single plane named `scalar`.

### What changes in the API

- The summary gains `"planes": [{"name", "k"}, ...]`, `"coupling"` (the matrix) and
  `"sinks": {"<plane>": <sink>, ...}`. `k` is the first plane's `k`. `total` adds, for each
  cell in canonical order, every plane's value in plane order, starting from `0.0`. `sink` is
  the per-plane sinks added in plane order starting from `0.0`.
- Each cell in `GET .../cells` gains `"values": [<plane 0>, <plane 1>, ...]`; `value` stays
  the first plane's value.
- `PUT .../cells` rows may carry `"plane": "<name>"` (default: the first plane); an unknown
  plane → `422 invalid_request`. `expected_total` accounting is unchanged.
- Transducer frames keep moving flux on the first plane only. Policy variables (`v`,
  `n0`–`n5`) read the first plane; a cell's gate applies to the flows of every plane.
- The audit gains `"planes": {"<name>": {"cells_total": ..., "sink_total": ...}, ...}` (cell
  values of that plane added in canonical order). `cells_total`, `sink_total`, `drift` and
  `ok` cover all planes together.

### The tick with planes

Propagate computes, from the values it sees, every plane's spatial flows (the existing rule
with the plane's own `k`, the field's boundary and the gates) and the coupling transfers.
Commit then computes each plane `p` of each cell `c` in this exact order:

```
acc = v_p[c]
... the spatial terms exactly as before, using plane p's values and k ...
for j in 0..N-1, j != p: acc = acc - (C[p][j] * v_p[c])
for j in 0..N-1, j != p: acc = acc + (C[j][p] * v_j[c])
new_p[c] = acc
```

Each plane's sink accumulates that plane's absorbed flows exactly as the single sink did.
