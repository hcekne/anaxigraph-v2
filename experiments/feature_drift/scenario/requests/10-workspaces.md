# Request 10 — Our second customer

Big one. We're onboarding our second customer, Northwind, next week, and we're deploying for
real. Everything must be separated per customer workspace, and nothing can be lost on restart.

## What I need

- Requests carry `X-Workspace: <id>`. **No header means the `default` workspace**, so existing
  clients keep working exactly as before.
- `POST /api/workspaces` with `{"id": "northwind", "name": "Northwind"}` → `201 {"id", "name"}`.
  `id` must match `[a-z0-9-]{2,32}` → otherwise `422 invalid_request`; existing id → `409 conflict`.
- `GET /api/workspaces` → `{"workspaces": [{"id": "default", "name": "Default"}, ...]}`;
  `GET /api/workspaces/{id}` → one, or `404 not_found`.
- An `X-Workspace` that does not exist → `404 unknown_workspace` on every endpoint except
  `/api/health` and the `/api/workspaces` endpoints.
- Per workspace: credentials (and therefore which provider account is used), integration status,
  capability availability, tickets and the audit log. One workspace must never see or use
  another's credentials or audit entries.
- Workspaces, credentials and the audit log survive a restart (keep them under `SWITCHBOARD_DATA_DIR`).
