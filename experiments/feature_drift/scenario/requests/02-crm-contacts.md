# Request 2 — Contacts from the CRM

Sales lives in PipeCRM. They want to find and add contacts from Switchboard without logging
into another tool.

## What I need

`PUT /api/credentials/crm` with `{"token": "<non-empty>"}` → `204`.

`GET /api/contacts?search=ann&limit=50`

- `search` optional (empty means everyone); `limit` integer 1–200, default 50.
- `200`: `{"contacts": [{"id": "c_a001", "name": "Ann Berg", "email": "...", "company": "Acme Ltd"}], "total": 12}`
  — `total` counts every match, even beyond `limit`. Keep PipeCRM's order. `company` is `null`
  when the contact has no organisation. PipeCRM paginates, so you must follow every page.

`POST /api/contacts` with `{"name": "...", "email": "...", "company": "..." }` (company optional)

- `201` with the created contact in the same shape as above.
- Missing or empty name, or an email without `@` → `422 invalid_request`. PipeCRM says the email already
  exists → `409 conflict`.

Errors otherwise follow the README conventions (`not_configured`, `upstream_auth`, `upstream_error`).

## PipeCRM API

Base `{EXTERNAL}/crm`. Header `Authorization: Token <token>`. Bad token → `401`.

- `GET /api/contacts?search=<text>&page=<n>` (page starts at 1) →
  `{"data": [{"id": "c_a001", "full_name": "Ann Berg", "email": "ann@x.com", "org": {"name": "Acme Ltd"}}], "page": 1, "next_page": 2}`.
  `org` may be `null`; `next_page` is `null` on the last page.
- `POST /api/contacts` with `{"full_name": ..., "email": ..., "org_name": ...}` → `201 {"data": <contact>}`;
  duplicate email → `409 {"error": "duplicate_email"}`.
