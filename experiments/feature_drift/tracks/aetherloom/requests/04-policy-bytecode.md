# Request 4 — Sandboxed Policy Bytecode Compiler (No eval/exec)

Compile declarative cell-routing expressions (.loom rules) into a bounded stack-machine bytecode with deterministic instruction fuel limits.

Operators want to throttle how freely flux moves through parts of a field ("slow the rim
down", "freeze cells above a threshold"). They write a small rule; we compile it and run it
for every cell in the Arbitrate phase. Rules come from customers, so they must never be able to
run arbitrary code: no `eval`, `exec`, `compile` or evaluating Python syntax trees.

## What I need

### The .loom rule language

A rule is one expression. Whitespace is free; `#` starts a comment to the end of the line.

```
expr    := sum (("<" | "<=" | ">" | ">=" | "==" | "!=") sum)*     # left-associative
sum     := product (("+" | "-") product)*
product := unary (("*" | "/") unary)*
unary   := "-" unary | primary
primary := NUMBER | NAME | NAME "(" expr ("," expr)* ")" | "(" expr ")"
NUMBER  := digits, optionally "." digits      (e.g. 3, 0.25)
```

- Variables: `v` (the cell's value), `q`, `r`, `ring` (`max(|q|, |r|, |q+r|)`), `tick` (the
  tick being run), `n0` … `n5` (the neighbour's value in direction 0–5, `0.0` if outside the
  field). Values are those the Propagate phase used.
- Functions: `min(a, b)`, `max(a, b)`, `abs(a)`, `if(c, a, b)` (`a` if `c != 0`, else `b`;
  only the chosen branch is evaluated).
- Arithmetic is IEEE double precision. Comparisons give `1.0` or `0.0`. Division by zero
  gives `0.0`.
- Anything else (unknown names or functions, wrong argument counts, other characters, syntax
  errors) is a compile error.

### Gates and the Arbitrate phase

With a policy installed, Arbitrate evaluates the rule once per cell to get the cell's
**gate** `g = min(max(result, 0.0), 1.0)` (a NaN result gives `0.0`). Each staged flow is then
scaled, so Commit computes:

```
in the field: acc = acc - ((k * (v[c] - v[n])) * min(g[c], g[n]))
missing:      acc = acc - ((k * v[c]) * g[c])          # and sink = sink + ((k * v[c]) * g[c])
```

Without a policy flows pass through unchanged (the request 1 formulas).

### Cost, fuel and stack

Evaluation cost is defined on the expression, independent of how you compile it:

| node | cost | stack depth |
| --- | --- | --- |
| number, variable | 1 | 1 |
| unary `-x`, `abs(x)` | cost(x) + 1 | depth(x) |
| binary operator, comparison, `min`, `max` | cost(a) + cost(b) + 1 | max(depth(a), depth(b) + 1) |
| `if(c, a, b)` | cost(c) + 1 + cost of the branch taken | max(depth(c), depth(a), depth(b)) |

The **static fuel** of a rule is its cost with every `if` taking its most expensive branch;
its **max stack** is the depth of the whole expression.

At run time each cell gets `fuel` units. If evaluating the rule for a cell would cost more
than `fuel`, that cell's gate is `0.0` (fail closed) and the evaluation counts as exhausted.

### Endpoints

- `POST /api/policies/compile` with `{"source": "if(v > 1, 0.5, 1)"}` →
  `200 {"static_fuel": 5, "max_stack": 2, ...}` (you may add fields, e.g. a bytecode listing).
  Compile errors → `422 compile_error` with a helpful message. `source` is a string of at most
  4000 characters (`422 invalid_request`).
- `PUT /api/fields/{id}/policy` with `{"source": "...", "fuel": 64}` installs a rule (`fuel`
  integer 1–10000, default 64) → `200` policy view:
  `{"source": "...", "fuel": 64, "static_fuel": 5, "max_stack": 2, "exhausted": 0}`.
  Compile error → `422 compile_error`; max stack above 16 → `422 stack_exceeded`. It is used by
  every Arbitrate phase that runs after it is installed.
- `GET /api/fields/{id}/policy` → the view (`exhausted` counts exhausted cell evaluations since
  installation); no policy → `404 no_policy`.
- `DELETE /api/fields/{id}/policy` → `204`; flows pass through unchanged again. No policy →
  `404 no_policy`.
