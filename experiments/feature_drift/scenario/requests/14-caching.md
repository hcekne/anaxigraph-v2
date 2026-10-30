# Request 14 — Caching

WeatherCo and the docs server are slow, and we pay per call everywhere. Add caching that each
workspace can tune per integration.

## What I need

Setting `cache_ttl_seconds`: `{"weatherco": 600, "docs": 60}` — integration → seconds (> 0).
Default `{}`, meaning no caching. Unknown integration names or non-positive values →
`422 invalid_request`.

- Cache read operations only: weather forecasts (any provider), contact search, docs search and
  get, calendar events and invoice lists. Never cache creating anything or listing tickets.
- Entries are per workspace and per operation input. A dedicated endpoint and its capability
  are the same operation and share entries (apply input defaults before keying). A cached answer is identical to a fresh one.
- A cache hit contacts no provider, costs no quota and adds no audit entry.
- Dedicated `GET` endpoints for those reads add a response header `X-Cache: hit` or `X-Cache: miss`
  when caching is configured for that integration.
- `DELETE /api/cache` clears the request workspace's cache → `204`. Storing new credentials for an
  integration clears that integration's cache in that workspace.
