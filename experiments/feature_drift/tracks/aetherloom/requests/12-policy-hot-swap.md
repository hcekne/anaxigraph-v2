# Request 12 — Hot-Swappable Bytecode Policy Reload with Zero Tick Drop

Allow staging a new .loom bytecode program and activating it at the next tick boundary only after static fuel and stack-depth verification passes.

Operators change routing rules on live fields. A tick must never run half under one rule and
half under another, and a rule that could run out of fuel or overflow the stack must never go
live at all.

## What I need

### Staging

`POST /api/fields/{id}/policy/stage` with `{"source": "...", "fuel": 64}` (`fuel` 1–10000,
default 64) →
`202 {"staged": {"source": "...", "fuel": 64, "static_fuel": 5, "max_stack": 2}, "activates_at_tick": 8}`.

- Compile errors → `422 compile_error`. Then **static verification**: the rule's static fuel
  must be `<= fuel` and its max stack `<= 16`; otherwise `422 verification_failed` (the message
  says which check failed). Nothing is staged on error.
- A staged rule becomes the field's active policy at the next **tick boundary**: when the
  field next starts an Emit phase, before anything else happens in that micro-phase. So
  `activates_at_tick` is the current tick when the field is at `sub_tick` 0, else the next
  tick. The tick in progress keeps using the old rule (or none) for its Arbitrate.
- Staging again before activation replaces the staged rule. Stepping is never blocked.
- `GET /api/fields/{id}/policy/staged` → the `staged` object, or `404 no_staged_policy`;
  `DELETE` on it → `204` (or `404 no_staged_policy`).
- When it activates, its `exhausted` counter starts at 0.

### Changes to the existing policy endpoints

- The policy view gains `"active_since"`: the tick whose Emit first ran with it (a rule
  installed with `PUT` at a boundary counts from the current tick).
- `PUT /api/fields/{id}/policy` and `DELETE /api/fields/{id}/policy` while a tick is in
  progress (`sub_tick` ≠ 0) → `409 mid_tick`; at a boundary they work as before.

### Boundary order

At a tick boundary, transitions happen in this order: (1) a staged policy activates,
(2) pending quarantines take effect, (3) queued frames are applied.

Staged rules are part of a field's state (checkpoints, restore, restarts).
