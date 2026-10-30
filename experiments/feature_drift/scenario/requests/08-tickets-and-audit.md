# Request 8 — Tickets, and an audit log for everything

Two things this time.

1. Let people raise and list tickets in TicketHub (another MCP server).
2. Compliance asked for this one, and it's not optional: **every request we send to any
   external system** must be recorded in an audit log we can query. All integrations,
   including the ones we already have, and every integration after this.

## What I need

`PUT /api/credentials/tickets` with `{"token": "<non-empty>"}` → `204`.

`POST /api/tickets` with `{"title": "...", "body": "...", "priority": "low" | "normal" | "high"}`
(priority defaults to `normal`; title and body required) → `201 {"id": "T-1001", "url": "...", "status": "open"}`.

`GET /api/tickets?status=open` (status optional) → `200 {"tickets": [{"id", "title", "priority", "status"}]}`.

`tickets` joins the integrations overview (kind `mcp`).

`GET /api/audit?integration=crm&limit=100` — both optional, `limit` 1–500 default 100 →

```json
{"entries": [{"id": "...", "at": "2026-01-15T10:00:00Z", "integration": "crm", "operation": "search contacts",
              "outcome": "ok", "status": 200, "duration_ms": 12.5}]}
```

- Newest first. Exactly **one entry per HTTP request sent to a provider**: retries, pagination,
  token requests, MCP `initialize`, health checks, all of it.
- `outcome` is `ok` for a 2xx response, otherwise `error`. `status` is the HTTP status, or `null`
  if no response arrived. `operation` is a short free-text description.
- An `integration` filter that matches nothing returns an empty list.

## TicketHub MCP server

Same protocol as the docs server: `POST {EXTERNAL}/mcp/tickets`, bearer token, `initialize` first.

- `create_ticket` arguments `{"title", "body", "priority"}` → text JSON `{"ticket": {"id", "url", "status"}}`.
- `list_tickets` arguments `{"status"?}` → text JSON `{"tickets": [{"id", "title", "priority", "status"}]}`.
