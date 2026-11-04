# Request 10 — Deterministic Checkpoint Snapshots & Time-Travel Replay

Save compact binary/JSON checkpoints every K ticks and support fast-forwarding from the nearest checkpoint to any target (tick, sub_tick).

We're deploying for real next week. Two things: nothing may be lost when the service
restarts, and operators want to rewind a field to an earlier moment and look at it again,
exactly as it was.

## What I need

### Persistence

Every field, with everything the API can show or that affects future results (cells of every
plane, sinks, the conservation ledger, queued frames, transducers with their windows,
agreements, status and violations, the policy and its counters, dead letters, checkpoints and
their schedule), survives a restart of the service. Keep it under `AETHERLOOM_DATA_DIR`.

### Checkpoints

A **checkpoint** is a complete copy of one field's state at a tick boundary (`sub_tick` 0) —
everything in the list above except the checkpoints and their schedule. Its storage format is
up to you (compact binary or JSON).

- `PUT /api/fields/{id}/checkpoints/schedule` with `{"every": K}` (integer 0–1000; 0 turns it
  off) → `200 {"every": K}`. With `K > 0`, whenever a Commit ends a tick at `T` with
  `T % K == 0`, a checkpoint at `T` is taken right away.
- `POST /api/fields/{id}/checkpoints` takes one now → `201 {"tick": T, "bytes": n}`. Mid-tick →
  `409 mid_tick`. A new checkpoint at a tick that already has one replaces it.
- `GET /api/fields/{id}/checkpoints` →
  `{"every": K, "checkpoints": [{"tick": 5, "bytes": 1832}, ...]}` ascending by tick; `bytes`
  is the stored size.

### Restore (time travel)

`POST /api/fields/{id}/restore` with `{"tick": T, "sub_tick": s}` (`T` integer `>= 0`, `s` 0–3,
default 0) → `200` field summary.

1. Take the checkpoint with the largest tick `<= T`; none → `404 no_checkpoint` (nothing
   changes).
2. Replace the field's state by it, and delete every checkpoint with a tick `> T`.
3. Run micro-phases from there until the field is at `(T, s)`, exactly as stepping would — the
   frames that were queued in the checkpoint are applied at the first Emit, nothing else
   arrives. Scheduled checkpoints are taken on the way as usual.

`T` may be ahead of the field's current tick (fast-forward). After a restore, stepping on gives
bit-identical results to stepping from that same state originally.
