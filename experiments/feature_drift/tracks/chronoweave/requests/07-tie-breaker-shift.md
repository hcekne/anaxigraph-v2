# Request 7 — Retroactive Clock-Skew Tie-Breaker Shift

Two assertions arriving with identical effective_t and asserted_at timestamps from different nodes must now break ties by deterministic BLAKE2b payload digest rather than insertion order.

We now ingest from several nodes, and "whichever reached this server first" is not something
two replicas agree on. Every replica must pick the same winner.

## What I need

Every assertion has a **payload digest**: BLAKE2b with a 32-byte digest (`hashlib.blake2b(...,
digest_size=32)`), lowercase hex, over the canonical JSON of
`{"id", "key", "value", "author", "node", "causal_parents"}` (exactly as stored). Records gain
`"digest": "<64 hex>"`. A promoted or imported copy keeps its digest.

**The tie-breaker changes everywhere.** Wherever an earlier request breaks a tie by "the order
in which this service recorded them", use the digest instead: the smaller digest sorts first,
so the larger digest wins. That covers at least:

- canonical order (assertion listings, state materialization, diff winners, `promoted` and
  `discarded` lists, and everything else defined in terms of canonical order);
- merge candidates and the `last_assertion_wins` full-tie rule (larger digest wins).

There is no other tie left: digests of different assertions differ because ids differ. This
applies to history already recorded too: a read now answers as if the digest rule had always
been in force.
