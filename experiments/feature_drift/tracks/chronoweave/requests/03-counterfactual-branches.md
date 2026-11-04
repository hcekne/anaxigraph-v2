# Request 3 — Counterfactual Branch Diffing & Three-Way Causal Merge

Allow forking a speculative branch from any historical (effective_t, assertion_t) coordinate, applying hypothetical assertions, and computing a minimal causal diff against main.

Analysts want "what if we had known X back in March?" and then, if it holds up, to promote the
result into `main`.

## What I need

### Fork from a coordinate

`POST /api/branches` accepts an optional `"from": {"effective_at": E, "asserted_at": A}`.

- The branch inherits what `main` knew as of `A`: the assertions and retractions recorded on
  `main` before the fork whose `asserted_at ≤ A`. Its clock starts at `A`.
- `E` is the counterfactual horizon: assertions written to the branch must have
  `effective_at ≥ E` → otherwise `422 before_fork`. `E` may be `null` (no limit).
- `from` must be an object with an integer `asserted_at` ≥ 0 and an `effective_at` that is an
  integer ≥ 0 or `null`; anything else, or `A` greater than `main`'s clock →
  `422 invalid_request`. Without `from` the behaviour is the one from request 0 (`A` = `main`'s
  clock, no horizon).
- Branch records gain `"from": {"effective_at": E, "asserted_at": A}` (`null` for `main`).

### Diff

`GET /api/branches/{branch}/diff?effective_at=E` (optional, default no bound) compares the
branch's state with `main`'s current state, both at `(E, no asserted_at bound)`:

```json
{"branch": "what-if",
 "changes": [{"key": "acct:7:limit",
              "main": {"value": 500, "assertion_id": "a-2"},
              "branch": {"value": 900, "assertion_id": "h-1"}}],
 "assertions": ["h-0", "h-1"],
 "conflicts": ["acct:7:limit"]}
```

- `changes`: every key whose value or winning assertion differs, sorted by key; a side with no
  entry is `null`.
- `assertions`: the minimal causal diff: the branch's own assertions (written to the branch,
  not inherited) that win a changed key on the branch side, plus every branch-own assertion
  they descend from, in canonical order.
- Diffing `main` itself → `409 main_only`; unknown branch → `404 not_found`.
- `conflicts` (sorted keys): keys changed on **both** sides since the fork: the branch has an
  own assertion on the key, and `main` has an assertion on the key that the branch did not
  inherit and that is not retracted.

### Merge (promotion)

`POST /api/branches/{branch}/merge` with `{"asserted_at": T}`:

- The branch must be `active`, else `409 branch_closed`; merging `main` → `409 main_only`.
  `T` must respect `main`'s clock (`409 clock_regression`).
- Any conflict → `409 merge_conflict` and nothing changes.
- Otherwise every branch-own assertion is copied to `main` in the branch's canonical order,
  keeping `id`, `key`, `value`, `effective_at`, `causal_parents`, `author` and `node`, with
  `asserted_at = T` (that is when `main` learns them).
- An id that already exists on `main` → `409 duplicate_assertion`. An assertion that would land
  in a sealed interval → `409 sealed_interval` (no overrides for merges). In every error case
  `main` is left exactly as it was.
- `200`:

```json
{"branch": "what-if", "asserted_at": 3000, "promoted": ["h-0", "h-1"], "discarded": []}
```

  `promoted` lists the copies in canonical order as they now stand on `main`; `discarded` is
  empty for now.

  After a successful merge the branch's status is `merged` and it accepts no more writes or
  merges (`409 branch_closed`); it can still be read.

All rules for writes to `main` (seals, clock, …) apply to what a merge writes, and the
promoted assertions behave on `main` like any other assertion from `T` on.
