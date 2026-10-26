# Shared external acceptance contract

Both implementations expose the same small external interface. Internal structure is unrestricted.
Return JSON, finite numbers and ordinary 4xx errors for malformed requests. Defaults use offline
demo data; no live provider is required to pass deterministic checks.

- `GET /` returns the working HTML application branded **Snow Rankings**.
- `GET /api/status` returns `{"status":"ok","data_mode":"demo","resorts":6}`. Extra fields are fine;
  `data_mode` may also be `live` or `mixed`, and `resorts` reports the actual catalogue count.
- `GET /api/resorts?limit=20&offset=0` returns `{items:[resort...],total:integer}`. Each resort has
  `id`, `name`, `country`, `latitude`, `longitude`, `elevation_m`. IDs are stable strings.
- `GET /api/search?origin=London&start_date=2026-01-15&days=5&currency=GBP&sort=cost&limit=20`
  returns `{items:[trip...],total:integer}`. Support `sort=best|cost|powder|travel|confidence`,
  optional nonnegative `budget`, optional `min_snowfall_cm` and `days=1..7`.
- Each trip contains `resort` (as above), `forecast` (exactly seven consecutive daily rows starting
  `start_date`), `travel`, `cost`, `score`, `powder_days`, `snowfall_total_cm`, `sources` and `warnings`.
- Each forecast row has `date` (`YYYY-MM-DD`), `snowfall_cm` (nonnegative number or null),
  `snow_depth_cm` (nonnegative number or null), `powder_probability` (0..1 or null),
  `probability_basis` (text). An uncalibrated heuristic must identify itself as such; precipitation
  probability alone is not a measured probability of finding powder.
- `travel` has `hours` (nonnegative number), `mode` and `basis` (text).
- `cost` has `currency`, `transport`, `accommodation`, `lifts`, `total` (nonnegative numbers),
  `basis` (`estimate` or `live_quote`) and `updated_at` (ISO time). Total equals the three components
  within 0.01 in that currency. Unknown live prices must not become invented live quotes.
- `sources` is a nonempty list of objects with `name`, `url`, `updated_at`, `kind` (such as
  `synthetic`, `forecast`, `observation` or `estimate`). `warnings` is a list of strings.
- `GET /api/resorts/{id}` returns the resort's details, a `forecast` and `sources`; unknown ID is404.

`sort=cost` and `travel` are ascending; `powder` (by `snowfall_total_cm`) and `confidence` are descending,
placing unknowns last. `best` is your documented transparent ranking. Cost and powder filters apply
to the values displayed. Forecast totals/powder days refer to the requested trip's `days`, not all7.

Support `GBP`, `EUR` and `USD` with a documented dated exchange-rate estimate in demo mode. Invalid
currencies, dates, impossible origins, out-of-range coordinates and days outside1..7 return4xx.
Use `origin=51.5074,-0.1278` for the coordinate journey. Limit/offset are bounded and validated.

Milestone4 adds `nights` (0..14, default `days`) and optional `max_travel_hours`. Accommodation uses
`nights`; lift passes still use `days`. Existing clients omitting these retain previous behavior.
