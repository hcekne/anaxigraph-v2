# Request 14 — Poisoned Speculative Merge & Partial Rollback

If a promoted speculative branch contains an assertion whose verifier depends on a concurrently retracted parent in main, abort promotion atomically and record a QuarantineReceipt.

Until now, promoting a branch whose work was built on something `main` has since taken back
went through, and the promoted assertions simply showed up tainted. That is no longer
acceptable: such a promotion must not touch `main` at all.

## What I need

When a merge is requested, after conflicts are resolved (request 6) and before anything else
is checked or written, look at the assertions that would be promoted. If any of them would be
tainted on `main` right after promotion (request 1 rules, checkpoints included) by an assertion
whose retraction the branch did **not** inherit (it was retracted on `main` after the fork, or
after the fork's `asserted_at`), the merge is **quarantined**:

- `main` is left exactly as it was: no assertions, no obligations, no events, no clock change.
  Not even part of the branch is promoted.
- A receipt is recorded and the response is `409 promotion_quarantined` (the message names the
  receipt id).
- The branch's status becomes `quarantined`; it accepts no more writes or merges
  (`409 branch_closed`) and, like a discarded branch, no longer counts as active for budgets
  (nor do its own assertions). It can still be read.

Receipt:

```json
{"id": "qr-1", "branch": "what-if", "asserted_at": 3000, "reason": "parent_retracted",
 "assertions": ["h-1", "h-2"], "retracted_parents": ["a-1"]}
```

- `asserted_at`: the merge's requested `asserted_at`.
- `assertions`: the would-be-promoted assertions that would have been tainted, canonical order.
- `retracted_parents`: the retracted `main` assertions that would have tainted them, sorted.
- Ids `qr-1`, `qr-2`, … per tenant.

`GET /api/quarantine` → `{"receipts": [...]}` in id order; `GET /api/quarantine/{id}` → one or
`404 not_found`. Receipts survive a restart.

Merges that are not poisoned behave exactly as before.
