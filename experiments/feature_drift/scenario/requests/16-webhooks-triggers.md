# Request 16 — React to CRM events

PipeCRM can push events to us now. When a contact is created in the CRM, we want a workflow to
run automatically (for example "raise an onboarding ticket").

## What I need

`PUT /api/credentials/crm` also accepts an optional `"webhook_secret"` alongside `token`.

`POST /api/webhooks/crm/{workspace_id}` (the workspace is in the path; PipeCRM sends no
`X-Workspace` header):

- Headers: `X-Pipe-Signature: sha256=<hex HMAC-SHA256 of the raw body, keyed with webhook_secret>` and
  `X-Pipe-Delivery: <unique delivery id>`.
- Body: `{"event": "contact.created", "data": {"id": ..., "full_name": ..., "email": ..., "org": {"name": ...} | null}}`.
- Unknown workspace → `404 unknown_workspace`. No secret stored, missing or wrong signature →
  `401 invalid_signature`.
- Valid → `202 {"accepted": true, "duplicate": false}`. PipeCRM retries deliveries: a delivery
  id already accepted for that workspace → `202 {"accepted": true, "duplicate": true}` and nothing runs again.
- Other event types are accepted (`202`) and ignored.

Triggers (per workspace, survive restarts):

- `POST /api/triggers` with `{"event": "crm.contact.created", "workflow_id": "...", "params": {"name": "{{event.name}}", "email": "{{event.email}}"}}`
  → `201` with its `id`. Unknown event or workflow → `422 invalid_request`. `GET /api/triggers` lists them.
- The event value is a contact in our shape: `{"id", "name", "email", "company"}`. `params` values
  use the workflow template rules with `event` as the root.
- An accepted `contact.created` delivery runs every matching trigger's workflow before responding,
  or within 5 seconds at most.
- `GET /api/triggers/runs` → `{"runs": [{"id", "trigger_id", "workflow_id", "status": "succeeded" | "failed", "at"}]}`, newest first.
