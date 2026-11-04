# Request 15 — Declarative Query DSL for Bitemporal Lineage Predicates

Add a compact query mini-language (e.g. `EFFECTIVE BETWEEN t1 AND t2 WHERE TAINTED BY author:x`) parsed into safe predicate ASTs.

Our analysts keep asking for one-off endpoints. Give them a small language instead. The
queries come from people typing into a box, so treat them as hostile input.

## What I need

`POST /api/query` with `{"q": "<query>", "branch": "main"}` (`branch` optional, default `main`)
→ `200 {"assertions": [record, ...]}`: the matching assertions on that branch, in canonical
order, as records seen at the query's knowledge time (fields as in
`GET /api/branches/{branch}/assertions/{id}?asserted_at=...`).

### Grammar

```
query      := [ "AS" "OF" INT ] [ "EFFECTIVE" "BETWEEN" INT "AND" INT ] [ "WHERE" expr ]
expr       := and_expr { "OR" and_expr }
and_expr   := not_expr { "AND" not_expr }
not_expr   := "NOT" not_expr | "(" expr ")" | predicate
predicate  := "KEY" "=" STRING
            | "KEY" "PREFIX" STRING
            | "AUTHOR" "=" STRING
            | "NODE" "=" STRING
            | "STATUS" "=" ( "active" | "tainted_pending_reconcile" | "retracted" )
            | "TAINTED" [ "BY" "author:" NAME ]
            | "DESCENDS" "FROM" STRING
```

- Keywords are case-insensitive; the three status names are bare lowercase words exactly as
  shown. `INT` is a non-negative decimal integer. `STRING` is
  double-quoted with `\"` and `\\` as the only escapes. `NAME` is `[A-Za-z0-9._-]+` and follows
  `author:` with no space. Whitespace separates tokens.
- `AS OF A`: the knowledge time (default: now). Only assertions asserted at or before `A` are
  considered; statuses are as of `A`.
- `EFFECTIVE BETWEEN F AND T`: `F ≤ effective_at ≤ T` (inclusive).
- `TAINTED`: status `tainted_pending_reconcile`. `TAINTED BY author:x`: tainted and at least one
  of its `tainted_by` assertions has author `x`.
- `DESCENDS FROM "id"`: the assertion is a descendant of that id (an unknown id matches nothing).
- An empty query matches every assertion. Precedence: `NOT` binds tighter than `AND`, which
  binds tighter than `OR`.
- Unknown `branch` → `404 not_found`; `q` missing or not a string → `422 invalid_request`.

Anything that does not match the grammar → `422 query_syntax`, with the 0-based character
offset of the problem in the message. Queries longer than 2000 characters →
`422 query_syntax`. A query can only ever read; nothing in it is executed or passed to a
database as text.
