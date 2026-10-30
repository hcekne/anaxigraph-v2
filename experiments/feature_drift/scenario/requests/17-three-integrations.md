# Request 17 — Three new integrations

Sales closed three deals this week, and each needs one integration. Add all three. They must
behave exactly like every other integration: credentials, the overview and health checks,
capabilities, workspaces, audit, quotas (1 unit per operation), caching of reads, switching off
and credential removal.

## FXRates (REST) — currency conversion

`PUT /api/credentials/fxrates` `{"app_id": "..."}`. Capability `fxrates.convert`, input `amount`* string
(positive decimal, at most 2 places), `from`* string, `to`* string. Also `GET /api/fx/convert?amount=100&from=EUR&to=NOK`.
Output: `{"amount": "100.00", "from": "EUR", "to": "NOK", "rate": "11.793478", "converted": "1179.35"}`.
`rate` = rates[to] / rates[from] rounded half-up to 6 places; `converted` = amount × rates[to] / rates[from]
rounded half-up to 2 places. Unknown currency → `422 unsupported_currency`.

API: `GET {EXTERNAL}/fxrates/latest.json?app_id=<app_id>` → `{"base": "USD", "rates": {"EUR": "0.92", ...}}`.
Bad app id → `401`.

## Lingo (MCP) — translation

`PUT /api/credentials/lingo` `{"token": "..."}`. Capability `lingo.translate`, input `text`* string,
`target`* string → `{"text": ..., "target": ..., "translation": ..., "source": "en"}`. No dedicated endpoint.

Server: `POST {EXTERNAL}/mcp/lingo`, same protocol as our other MCP servers. Tool `translate`,
arguments `{"text", "target_lang"}` → text JSON `{"translation": ..., "source_lang": ...}`.

## ShipTrack (REST) — parcel tracking

`PUT /api/credentials/shiptrack` `{"username": "...", "password": "..."}`. Capability `shiptrack.track`,
input `code`* string. Also `GET /api/shipments/{code}`. Output:

```json
{"code": "SH123", "status": "in_transit", "delivered": false,
 "last_event": {"at": "...", "location": "Gothenburg", "description": "In transit"},
 "events": [{"at": "...", "location": "Oslo", "description": "Picked up"}, ...]}
```

Events oldest first; `last_event` is the newest or `null`. Unknown code → `404 not_found`.

API: `GET {EXTERNAL}/shiptrack/v2/track/{code}` with HTTP Basic auth →
`{"tracking_code": ..., "state": "in_transit" | "delivered" | ..., "events": [{"ts", "place", "text"}]}` (oldest first).
Unknown code → `404`. Bad credentials → `401`.
