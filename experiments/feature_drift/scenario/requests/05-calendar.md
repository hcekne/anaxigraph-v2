# Request 5 — Crew calendars

Crews need to see their schedule. We use TeamCal. TeamCal uses OAuth client credentials and
bills us for token requests, so reuse a token until TeamCal stops accepting it.

## What I need

`PUT /api/credentials/calendar` with `{"client_id": "...", "client_secret": "..."}` (both non-empty) → `204`.

`GET /api/calendar/events?from=2026-01-12&to=2026-01-18`

- Both dates required, `from <= to`, at most 31 days apart (`to - from <= 31` days); else `422 invalid_request`.
- `200 {"events": [{"id": "ev-a-1", "title": "...", "start": "2026-01-12T09:00:00Z", "end": "...", "location": "Depot"}]}`
  sorted by `start`, then `id`. `location` may be `null`. Include events starting on any day in the range.

`calendar` joins the integrations overview (kind `rest`).

## TeamCal API

Base `{EXTERNAL}/calendar`.

- `POST /oauth/token` with JSON or form body
  `{"grant_type": "client_credentials", "client_id": ..., "client_secret": ...}` →
  `{"access_token": "...", "token_type": "Bearer", "expires_in": 3600}`. Bad client → `401`.
- `GET /v1/events?start=<date>&end=<date>[&cursor=<c>]` with `Authorization: Bearer <token>` →
  `{"items": [{"uid": ..., "summary": ..., "starts_at": ..., "ends_at": ..., "where": "Depot" | null}], "next": "<cursor>" | null}`.
  Follow `next` until `null`. Tokens can be revoked before `expires_in`: then `401 {"error": "token_expired"}`;
  fetch a new token and retry once.
