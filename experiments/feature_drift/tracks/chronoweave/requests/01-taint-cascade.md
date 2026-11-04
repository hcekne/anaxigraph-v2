# Request 1 — Retroactive Tombstone Cascade & Taint Propagation

When an assertion is retracted at assertion_t2 for an earlier effective_t1, automatically mark all causally dependent downstream assertions as tainted_pending_reconcile unless insulated by a checkpoint.

Our reconciliation team needs to see what is built on top of something we took back. They also
need yesterday's view to stay yesterday's view.

## What I need

**Descendants.** B is a descendant of X when X is in B's `causal_parents`, or in the parents of
one of those, and so on.

**Checkpoints.** `POST /api/branches/main/checkpoints` with
`{"assertion_id": "c-1", "asserted_at": 1300}` → `201 {"assertion_id": "c-1", "asserted_at": 1300}`.
It declares that `c-1` was independently verified. It is a write (clock rule applies →
`409 clock_regression`). Unknown or not-visible assertion → `404 not_found`; retracted →
`409 already_retracted`; already checkpointed → `409 conflict`. `main` only (`409 main_only`).

**Taint.** As of a knowledge time `A`, assertion B is *tainted by* X when X was retracted at
`t2 ≤ A`, B is a descendant of X, and there is at least one causal path X → … → B on which no
assertion after X (B included) had a checkpoint recorded at or before `t2`. A checkpoint
recorded after the retraction does not clear existing taint.

**Reconcile.** `POST /api/branches/main/assertions/{id}/reconcile` with `{"asserted_at": 1600}`
→ `200` with the record. From then on B is no longer tainted by retractions recorded at or
before that time (later retractions can taint it again). Reconcile affects only B itself, not its descendants. It is a write (clock rule). Unknown →
`404 not_found`; not currently tainted → `409 not_tainted`; `main` only (`409 main_only`).

**Status.** Records now carry two more fields, computed as of the knowledge time of the read:

- `status`: `"retracted"` if retracted, else `"tainted_pending_reconcile"` if tainted by at
  least one assertion, else `"active"`.
- `tainted_by`: the ids that currently taint it, sorted, each listed once (`[]` when none).

`GET /api/branches/{branch}/assertions/{id}?asserted_at=A` returns the record as of `A`
(default: now). As of `A`, `retracted_at` is `null` if the retraction came after `A`; an
assertion not yet asserted at `A` → `404 not_found`.

`GET /api/branches/{branch}/assertions` accepts `asserted_at=A` too (records as of `A`, only
those asserted by then) and `status=active|tainted_pending_reconcile|retracted` as a filter.

State entries (`GET .../state`) gain `"status"` (`"active"` or `"tainted_pending_reconcile"`,
as of that coordinate's `asserted_at`). Tainted assertions stay in the state; only their status
changes.

Reads never change anything: asking about an earlier `asserted_at` gives the same answer
before and after later retractions, checkpoints and reconciliations.
