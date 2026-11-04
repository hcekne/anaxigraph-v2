# Request 20 — Cross-Epoch Diamond Causal Cycle & Compaction Race

Handle a diamond causal graph where two speculative branches merge across a compacted horizon boundary while a retroactive retraction arrives on the shared ancestor.

We hit this in production: two analysts each branched off the same assertion, a compaction ran
in between, one promotion went in, then the shared ancestor was retracted, and finally someone
promoted the second branch and joined both lines in `main`. Some of our tooling looped forever,
some lost the compacted part of the lineage. Make this airtight.

## What I need

### Diamonds are fine; cycles are not

A diamond (A → B1 → D and A → B2 → D) is a valid causal graph. A **causal cycle** is an
assertion that would be its own ancestor. From now on every place that accepts causal parents
rejects cycles with `422 causal_cycle` and stores nothing:

- an assertion (on any branch, or in a simulation) that lists its own id in `causal_parents`
  (this used to be `422 unknown_parent`);
- bundle verification and import (request 10) when the bundle's parent links contain a cycle
  of any length. This check comes before the other bundle checks except gzip/JSON/shape
  and digest.

### Lineage

`GET /api/branches/{branch}/assertions/{id}/lineage?asserted_at=A` (optional, default now) →

```json
{"id": "d", "ancestors": ["a", "b1", "b2"], "descendants": []}
```

every ancestor and every descendant as of `A` (assertions asserted at or before `A`, retracted
ones included), each exactly once, in canonical order. Unknown id → `404 not_found`. Lineage
crosses compacted intervals unchanged: compacted assertions are still ancestors and
descendants.

### The race, made explicit

Everything already defined keeps holding in this situation, and must terminate quickly
however the paths overlap:

- taint reaches every descendant of a retracted assertion along every non-insulated path, but
  each tainted assertion lists each retracted assertion once in `tainted_by`; a compacted
  assertion insulates like a checkpoint from its compaction on;
- a `grant.invalidated` event is sent once per grant per retraction, never once per path;
- a branch forked before a compaction and merged after it promotes normally when nothing it
  promotes would be tainted, and is quarantined (request 14) when something would be — in
  both cases the compacted assertions, the compaction summaries and seal verification are
  untouched, and reads at earlier coordinates still answer as before;
- retracting a compacted assertion is still refused with `409 compacted`.
