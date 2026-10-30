# Request 1 — Weather for our crews

First real feature! Our customers keep asking what the weather will be where their crews are
working. We have a WeatherCo account. Give me an endpoint that returns the forecast for a city.
The API key comes in through our API (we rotate it), not through config.

## What I need

`PUT /api/credentials/weatherco` with `{"api_key": "<non-empty string>"}` stores the key → `204`.
Empty or missing key → `422 invalid_request`.

`GET /api/weather?city=Oslo&days=3`

- `city` is required; `days` is an integer 1–7, default 3. Otherwise `422 invalid_request`.
- `200`:

```json
{"city": "Oslo", "country": "NO",
 "days": [{"date": "2026-01-15", "high_c": -2.0, "low_c": -8.5, "precip_mm": 1.2, "summary": "Light snow"}]}
```

  `city` and `country` are what WeatherCo calls the place; `days` has exactly `days` entries in
  date order.
- No key stored yet → `409 not_configured`. WeatherCo does not know the city → `404 unknown_city`.
  WeatherCo rejects the key → `502 upstream_auth`. Any other WeatherCo failure → `502 upstream_error`.

## WeatherCo API (v1)

Base: `{EXTERNAL}/weatherco`. Every request sends header `X-Api-Key: <key>`. Bad key → `401`.

- `GET /v1/geocode?q=<name>` → `{"results": [{"name": "Oslo", "country": "NO", "lat": 59.91, "lon": 10.75}]}`.
  Empty `results` means unknown place; the first result is the best match.
- `GET /v1/forecast?lat=<lat>&lon=<lon>&days=<n>` →
  `{"location": {...}, "daily": [{"date": "2026-01-15", "temp_max_c": -2.0, "temp_min_c": -8.5, "precip_mm": 1.2, "conditions": "Light snow"}]}`
