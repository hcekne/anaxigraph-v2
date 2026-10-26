# Snow Rankings

Build a useful working website that answers: **Where can I reach good powder soon, and what will
the trip cost?** Users supply their origin, travel dates and preferences; compare ski destinations
by recent snow, the next seven days of forecast snow, travel time, powder opportunity and total
estimated trip cost. Give humans a clear, attractive frontend backed by a real API and database.

Use Python 3.11+, FastAPI, SQLite and vanilla HTML/CSS/JavaScript. Run through uv. Choose the internal
architecture yourself. Build good, maintainable software with clear responsibilities, useful tests
and understandable interfaces. This is a real product prototype, not a deliberately poor baseline.

## Required product behavior

- Origin search accepts a city or latitude/longitude. A sensible London default keeps the demo usable.
- Search and compare global destinations; start with the supplied representative seed data and
  support importing thousands of resort records without changing application code.
- Show a seven-day daily forecast, recent snowfall and snow depth where known. Missing measurements
  remain unknown. Distinguish forecast/model output, observations and synthetic demonstration data.
- Sort and filter by total trip cost, travel time, forecast snowfall, expected powder days and a
  clearly explained confidence/likelihood signal. A weather forecast cannot guarantee powder.
- Explain each trip's transport, accommodation and lift-pass costs, currency, dates and assumptions.
  Prototype estimates must be visibly estimates; do not call synthetic or cached values live quotes.
- Provide a responsive destination table/cards plus a useful geographic overview (a lightweight SVG
  world map is sufficient). Selecting a destination shows forecasts, costs, sources and update time.
- The application works offline in demo mode. Add a separately selectable Open-Meteo forecast
  integration using actual HTTP, bounded timeouts/caching and clear fallback/error states.
- Keep provider attribution and source provenance visible. Do not buy services, create accounts,
  enter credentials, make bookings or enable paid APIs. Affiliate and advertising slots are future
  extension points, clearly labelled if shown; they must not silently bias organic ranking.

## Development contract

Expose one command documented in README that starts the app on a configurable host/port. No Docker
or credentials may be required for the default offline journey. Tests should run without network.
Commit each milestone and record actual validation. Do not inspect the other implementation.

The shared acceptance interface is in `api-contract.md`. The task sequence is `tasks/01` through
`tasks/04`; finish in order. Preserve prior functionality during subsequent changes. The provided
seed data is explicitly synthetic and is not evidence of current snow or bookable prices.
