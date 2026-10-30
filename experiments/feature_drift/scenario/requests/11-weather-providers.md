# Request 11 — WeatherCo v2, and SkyCast for Northwind

Two weather changes landed on my desk the same day:

1. WeatherCo is retiring v1. Forecast calls already return `410 Gone` for some accounts. Move to v2.
2. Northwind already pays for SkyCast and wants that instead of WeatherCo.

So weather needs a per-workspace provider choice. `GET /api/weather` must keep returning
exactly the same JSON shape whichever provider is used.

## What I need

Workspace settings:

- `GET /api/workspaces/{id}/settings` → all settings with their defaults, e.g. `{"weather_provider": "weatherco"}`.
- `PUT /api/workspaces/{id}/settings` with some settings → merges them (each top-level key you
  send replaces that key) and returns the full settings. Invalid values → `422 invalid_request`.
  Unknown workspace → `404 not_found`. Settings survive restarts.
- `weather_provider`: `weatherco` (default) or `skycast`.

`PUT /api/credentials/skycast` with `{"client_token": "<non-empty>"}` → `204`. `skycast` joins
the integrations overview (kind `rest`).

`GET /api/weather` uses the workspace's provider; that provider unconfigured → `409 not_configured`.

New capabilities: `skycast.forecast` (same input/output as `weatherco.forecast`) and
`weather.forecast` (same again, using the workspace's provider; its catalogue `integration` is
`weather`, and quotas, caching and switching off apply to the provider actually used).
`weatherco.forecast` stays.

## WeatherCo v2

Same base and `X-Api-Key` header. `GET /v2/forecast?q=<city>&days=<n>` (no geocoding step) →
`{"place": {"name": "Oslo", "country_code": "NO"}, "forecast": [{"day": "2026-01-15", "t_max_k": 271.15, "t_min_k": 264.65, "rain_mm": 0.0, "snow_mm": 1.2, "text": "Light snow"}]}`.
Unknown city → `404 {"error": "place_not_found"}`. Convert Kelvin to Celsius rounded to one decimal;
`precip_mm` is rain plus snow, rounded to one decimal.

## SkyCast API

Base `{EXTERNAL}/skycast`. `POST /api/forecast` with `Authorization: Bearer <client_token>` and
`{"location": "Oslo", "days": 3}` → `{"city": "Oslo", "country": "NO", "days": [{"date", "max", "min", "precipitation", "description"}]}`
(Celsius, mm). Unknown location → `404`. Bad token → `401`.
