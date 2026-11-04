# Request 7 — Coordinate Reflection & Reflective Boundary Inversion

Switch boundary cells from absorbing sinks to reflective barriers that invert radial flux vectors on odd sub-ticks.

Our physicists were unhappy: real containment vessels don't leak at the rim. From now on a
field's boundary reflects flux back inwards by default. Some customers still model open
systems, so the absorbing boundary stays available on request.

## What I need

- `POST /api/fields` accepts `"boundary": "reflective"` or `"absorbing"`; **the default is now
  `reflective`**. Any other value → `422 invalid_request`. The field summary gains
  `"boundary"`. Absorbing fields behave exactly as before.
- In a reflective field nothing ever reaches the sink (`sink` stays `0.0`). Instead, the
  outward flow of a boundary cell is inverted: for a cell `c` whose neighbour in direction `d`
  is outside the field, the flow `k * v[c]` (scaled by `g[c]` when a policy is installed,
  exactly like a sink flow) goes to the cell on the **opposite side**, `m` = the neighbour of
  `c` in direction `(d + 3) mod 6`. If `m` is outside the field too, that flow is zero.
- Like every other flow, a reflected flow is computed in Propagate (sub-tick 1) from the
  values Propagate sees, scaled in Arbitrate and applied in Commit (sub-tick 3).
- Exact order for a reflective field (bit-identical results are still required). With
  `F(x) = (k * v[x])`, or `((k * v[x]) * g[x])` with a policy:

  ```
  for each cell c:
      acc = v[c]
      for d in 0..5:
          n = neighbour of c in direction d
          if n is in the field: acc = acc - (edge flow, as before)
          elif the neighbour of c in direction (d+3) mod 6 is in the field: acc = acc - F(c)
      for e in 0..5:
          s = neighbour of c in direction e
          if s is in the field and the neighbour of s in direction e is not: acc = acc + F(s)
      new[c] = acc
  ```

  (The second loop collects the flows reflected *into* `c`: `s` reflects its direction-`e`
  outflow onto the cell opposite, which is `c`.)
- The conservation audit holds for both kinds of field at every sub-tick.
