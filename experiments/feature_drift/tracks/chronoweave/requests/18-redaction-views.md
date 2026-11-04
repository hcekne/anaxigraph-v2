# Request 18 — Zero-Knowledge Redaction Views (Role-Scoped Provenance Masking)

Allow auditors without payload clearance to verify causal graph integrity and timestamp monotonicity using blinded commitment hashes.

External auditors must be able to check that our causal history is intact and that time
never runs backwards, without seeing a single key, value, id, author or node. (No
zero-knowledge proof library here: salted hash commitments are what we can do.)

## What I need

### Roles

Requests carry `X-Role: owner | auditor` (no header = `owner`; anything else →
`422 invalid_request`). An **auditor** may only call `GET /api/health`,
`GET /api/audit/graph` and `POST /api/audit/verify`. Every other endpoint answers an auditor with
`403 payload_clearance_required`. Nothing an auditor receives, error messages included, may
contain any assertion's id, key, value, author or node.

### Commitments

When an assertion first lands on `main` (directly, by merge or by import) it gets a random
**salt** (32 lowercase hex characters) that never changes. Its commitment is SHA-256 over the
UTF-8 text `<salt> + "\n" + <canonical JSON of {"id", "key", "value", "author", "node"}>`.

Owners can open a commitment: `GET /api/branches/main/assertions/{id}/opening` →
`{"commitment": "<64 hex>", "salt": "<32 hex>", "payload": {"id", "key", "value", "author", "node"}}`.

### Redacted graph

`GET /api/audit/graph?asserted_at=A` (optional, default now) → the causal graph of `main` as of
`A` (assertions asserted at or before `A`, retracted ones included):

```json
{"asserted_at": 5000,
 "nodes": [{"commitment": "<64 hex>", "effective_at": 10, "asserted_at": 20,
            "retracted_at": null, "parents": ["<64 hex>", ...]}],
 "root": "<64 hex>"}
```

- `asserted_at` echoes the parameter (`null` when omitted).
- `parents`: the parents' commitments, sorted. `retracted_at` as of `A`.
- `nodes` sorted by `asserted_at`, then `commitment`.
- `root`: SHA-256 of the canonical JSON of the `nodes` list.

### Verification

`POST /api/audit/verify` with a graph of that shape (`asserted_at` may be omitted) → `200
{"valid": bool, "violations": [...]}`. It checks only the submitted document (the order of `nodes` is not checked):

- `dangling_parent`: a parent commitment that is not a node in the graph;
- `timestamp_regression`: a node whose `asserted_at` is smaller than one of its parents';
- `retraction_before_assertion`: `retracted_at` not `null` and smaller than `asserted_at`;
- `root_mismatch`: `root` is not the hash of `nodes`.

Each violation is `{"commitment": <node commitment, or null for root_mismatch>, "problem": "<name>"}`,
one per (node, problem) pair, sorted by commitment (`null` first), then problem.
`valid` is `true` when there are none. A body that is not a graph of that shape →
`422 invalid_request`.
