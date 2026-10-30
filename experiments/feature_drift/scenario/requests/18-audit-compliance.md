# Request 18 — Compliance, round two

Compliance came back on the audit log:

1. They need to see *what* we called, but it must never contain secrets or personal data.
2. They want an export.
3. They want retention limits.

## What I need

Each audit entry gains `"request": {"method": "GET", "path": "/crm/api/contacts", "query": {"search": "[EMAIL]", "page": "1"}}`
— the provider path relative to `{EXTERNAL}` and its query parameters.

- Query values whose parameter name contains `key`, `token`, `secret`, `password` or `app_id`
  become `"[REDACTED]"`. Every e-mail address anywhere in `path` or `query` becomes `[EMAIL]`.
- Headers and bodies are never recorded. No credential value may appear anywhere in the audit
  log or its export, including inside paths (for example a Ledgerly account id becomes `[REDACTED]`).

`GET /api/audit/export` (optional `integration` filter) → `application/x-ndjson`, one entry per
line, **oldest first**.

Setting `audit_retention`: integer ≥ 10, default 10000 — keep only the newest that many entries
per workspace.
