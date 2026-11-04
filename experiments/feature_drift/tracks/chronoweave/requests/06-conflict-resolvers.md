# Request 6 — Pluggable Conflict Resolver Family (3 Initial Strategies)

Add three concurrent branch merge conflict resolvers: LastAssertionWins, CausalDepthPriority, and QuorumWitnessResolver.

Today a single conflicting key blocks a whole promotion. Let the person merging choose how
conflicts are settled. Expect more strategies later.

## What I need

`GET /api/resolvers` → `{"resolvers": [{"name": "causal_depth_priority"}, {"name": "last_assertion_wins"}, {"name": "quorum_witness"}]}`
sorted by name.

`POST /api/branches/{branch}/merge` accepts `"resolver": "<name>"` and, for resolvers that take
options, `"options": {...}`. Unknown resolver → `422 unknown_resolver`; bad options →
`422 invalid_request`. Without `resolver`, conflicts still give `409 merge_conflict`.

For each conflicting key (as defined by the diff) the resolver picks a winning **side**:

- **branch**: the branch's own assertions on that key are promoted.
- **main**: the branch's own assertions on that key are not promoted; they are reported in
  `discarded`. Any branch-own assertion that descends from a discarded one is discarded too.

Each side has one **candidate** for the key: on the main side, among `main`'s assertions on the
key that the branch did not inherit and that are not retracted; on the branch side, among the
branch's own assertions on the key. The candidate is the one that sorts last by
`(asserted_at, effective_at)`, ties broken like canonical order.

Strategies:

- `last_assertion_wins`: compare the two candidates by `(asserted_at, effective_at)`; the
  later one's side wins. On a full tie, the candidate recorded later by this service wins.
- `causal_depth_priority`: the candidate with the greater causal depth wins (an assertion
  without parents has depth 0; otherwise 1 + the largest depth among its parents, measured on
  the candidate's own side). Equal depth → decide as `last_assertion_wins`.
- `quorum_witness`, options `{"quorum": n}` (integer ≥ 1, required): a side's witness count
  is the number of distinct `node`s among that side's assertions on the key whose `value`
  equals its candidate's value. The branch wins only if its count is ≥ `quorum` and greater
  than main's count; otherwise main wins.

The merge response lists `promoted` (canonical order on `main`), `discarded` (canonical order on
the branch) and adds
`"resolutions": [{"key": "...", "winner": "branch" | "main"}]` sorted by key (`[]` when there
were no conflicts). Every other merge rule from request 3 still applies to what is promoted.
