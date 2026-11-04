# Request 4 — Non-Monotonic Grant Inversion Rules

Support non-monotonic derived rules where a late-arriving negative assertion inverts a previously derived capability grant and emits a CompensationObligation record.

Example: Alice may approve payments while she is `role:alice = "approver"`... unless a
suspension shows up later, back-dated to before some approvals. Those approvals then need
compensating, and we need a record saying exactly for which period.

## What I need

### Rules

`POST /api/rules` with

```json
{"id": "alice-pays", "capability": "approve_payments",
 "requires": [{"key": "role:alice", "equals": "approver"}],
 "unless": [{"key": "suspended:alice", "equals": true}]}
```

→ `201` echoing the rule. `id` matches `[A-Za-z0-9][A-Za-z0-9._:-]{0,63}` (duplicate →
`409 conflict`); `capability` non-empty string; `requires` a non-empty list and `unless` a
list (default `[]`) of `{"key": non-empty string, "equals": any JSON}` (`equals` is required; it may be `null`). Otherwise
`422 invalid_request`. `GET /api/rules` → `{"rules": [...]}` sorted by `id`.

Rules apply to `main`. A rule **holds** at a coordinate `(E, A)` when, in `main`'s state at
`(E, A)`, every `requires` key has a value equal to its `equals` and no `unless` key has a value
equal to its `equals` (a missing key equals nothing).

`GET /api/grants?effective_at=E&asserted_at=A` (both optional, no bound when omitted) →
`{"grants": [{"rule": "alice-pays", "capability": "approve_payments"}]}`: the rules that hold,
sorted by rule id.

### Compensation obligations

Holding is a function of effective time: for a fixed knowledge time, a rule holds on some
set of effective-time intervals. Every write to `main` (assertion, retraction, merge, or
anything later requests add) compares, for every rule that existed before the write, the
effective times where the rule held as known just before the write with those where it holds
just after. Each maximal interval of effective time where it **used to hold and no longer
does** produces one obligation:

```json
{"id": "obl-1", "rule": "alice-pays", "capability": "approve_payments",
 "effective_from": 1500, "effective_to": 1800, "asserted_at": 2100, "cause": "susp-1"}
```

- `effective_from` is inclusive, `effective_to` exclusive (`null` = open-ended).
- `cause`: the id of the assertion written or retracted; for a merge, `"merge:<branch>"`.
- `asserted_at`: the write's `asserted_at`.
- Ids are `obl-1`, `obl-2`, … in creation order; one write's obligations are ordered by rule
  id, then `effective_from`.
- Writes that only add holding time, or remove none, create nothing.

`GET /api/obligations?rule=<id>` (`rule` optional) → `{"obligations": [...]}` in id order.

Grants are derived, never stored; asking about an earlier `asserted_at` gives the same answer
it gave back then.
