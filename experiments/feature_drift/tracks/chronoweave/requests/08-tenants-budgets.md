# Request 8 — Tenant-Isolated Bitemporal Namespaces & Quota Budgets

Isolate assertions, speculative branches, and epoch seals by tenant_id with configurable per-tenant active branch and assertion byte budgets.

We're onboarding our second customer. Nothing of one tenant may ever be visible to, or
affected by, another, and one tenant must not be able to eat all our disk.

## What I need

- Requests carry `X-Tenant: <id>`. **No header means the `default` tenant**, so existing
  clients keep working exactly as before.
- `POST /api/tenants` with
  `{"id": "northwind", "budgets": {"max_active_branches": 2, "max_assertion_bytes": 10000}}`
  (`budgets` optional; each budget an integer ≥ 0 or `null` = unlimited, default `null`) →
  `201`:

  ```json
  {"id": "northwind", "budgets": {"max_active_branches": 2, "max_assertion_bytes": 10000},
   "usage": {"active_branches": 0, "assertion_bytes": 0}}
  ```

  `id` must match `[a-z0-9-]{2,32}` → otherwise `422 invalid_request`; existing id → `409 conflict`.
- `GET /api/tenants` → `{"tenants": [...]}` sorted by id (`default` is always there, unlimited);
  `GET /api/tenants/{id}` → one, or `404 not_found`.
  `PUT /api/tenants/{id}/budgets` with a full budgets object as the body, e.g.
  `{"max_active_branches": 3, "max_assertion_bytes": null}` (both keys required) → `200` with the tenant.
- An `X-Tenant` that does not exist → `404 unknown_tenant` on every endpoint except
  `/api/health` and the `/api/tenants` endpoints.
- Everything is per tenant: assertions, branches (names may repeat across tenants), clocks,
  checkpoints, seals, the authority secret, rules, grants and obligations, and everything later
  requests add unless they say otherwise.

### Budgets

- `active_branches`: branches other than `main` whose status is `active`. Creating a branch
  when the count is already ≥ `max_active_branches` → `409 budget_exceeded`.
- `assertion_bytes`: the sum, over every assertion stored on `main` and every own assertion of
  an `active` branch, of the length in bytes of the canonical JSON of its `value`. A write
  (including a merge) that would take the sum above `max_assertion_bytes` →
  `409 budget_exceeded`, and nothing is stored. A merge only moves bytes: the merged branch's own
  assertions stop counting in the same write in which their copies start counting on `main`.
- `DELETE /api/branches/{branch}` discards a branch → `200 {"name": ..., "status": "discarded", ...}`;
  it then accepts no writes or merges (`409 branch_closed`) and its own assertions no longer
  count. `main` cannot be discarded (`409 main_only`); a closed branch → `409 branch_closed`.
- Lowering a budget below current usage is allowed; it only stops further growth.

Tenants, their budgets and all their data survive a restart.
