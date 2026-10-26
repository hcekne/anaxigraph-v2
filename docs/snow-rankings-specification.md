# Snow Rankings product specification

Snow Rankings helps someone answer **Where can I get good powder soon, how quickly can I get there,
and what will the whole trip cost?** It combines destination discovery, current/recent snow signals,
seven-day forecasts and transparent travel estimates in one usable website. The same specification
is implemented independently in the treatment and control repositories.

## User journey

1. Enter a city or coordinates, departure date, ski days, party preferences and spending currency.
2. Compare global ski destinations by powder opportunity, forecast snowfall, travel time, expected
   powder days and total trip cost. Start with a sensible default search rather than an empty page.
3. Change sorting and filters, explore the geographic view and select a destination.
4. Read its seven-day forecast, recent snow/depth information, data times, confidence explanation,
   route assumptions and cost breakdown. Distinguish unknowns from zeros and forecasts from observations.
5. Follow an appropriate resort/travel link. Future bookable and affiliate offers must disclose their
   status and must not silently change organic rankings.

The user wants practical answers such as an estimated five-day trip cost, with transport,
accommodation and lift passes shown separately. A prototype must not invent a bookable price or
claim a forecast guarantees fresh snow. Travel hours and price assumptions must remain inspectable.

## Functional requirements

| Area | Required behavior |
| --- | --- |
| Catalogue | Stable resort IDs; names, country/region, coordinates, representative elevation and source identity; searchable/paged storage and JSON import for thousands of locations. |
| Origins | City and latitude/longitude input, validation and a London demo default; show the actual resolved origin. |
| Snow | Recent snowfall/depth when available, seven consecutive daily forecasts and explicit unknown values. Keep observed, modeled, derived and synthetic values distinguishable. |
| Powder | Forecast snowfall totals for the requested ski days, a documented powder-day threshold, and an explained likelihood/confidence signal. Avoid claiming precipitation probability is powder probability. |
| Travel | Comparable origin-to-destination duration with mode, assumptions and source/basis; distinguish an estimate from a scheduled/bookable itinerary. |
| Costs | Transport, accommodation and lift passes; consistent total and currency; dated assumptions or quote validity; GBP/EUR/USD support for the prototype. |
| Ranking | Best overall, cheapest, most forecast snow, fastest and confidence; deterministic ties; unknown confidence last; explain the overall score. |
| Filters | Budget, snowfall minimum, date/duration and later travel-time maximum; filtering must use the same values the user sees. |
| Interface | Responsive frontend, useful results table/cards, geographic overview, selected-destination details, seven-day forecast, source times and loading/empty/error states. |
| Resilience | Offline demo mode, optional live forecast adapter, bounded requests/cache, visible stale/fallback states and repeatable tests without network credentials. |
| Provenance | Every source names its provider, data kind and update time; synthetic data and estimated prices remain visibly labelled. |

The first implementation uses Python/FastAPI, SQLite and vanilla HTML/CSS/JavaScript. The comparison
holds these technology choices equal. Internal responsibility boundaries and design decisions remain
the coding agents' work. No framework, provider account, cloud deployment or paid API is required.

The common external API and deterministic seed are in
[`experiments/snow_rankings/api-contract.md`](../experiments/snow_rankings/api-contract.md) and
[`seed.json`](../experiments/snow_rankings/seed.json). The seed contains six globally distributed,
explicitly synthetic examples, not real current conditions. Catalogue import must scale independently
of that small acceptance fixture. A low demo count must never be marketed as exhaustive world coverage.

## Data sources and uncertainty

Open-Meteo's forecast API exposes snowfall, snow depth and daily snowfall aggregation; its default
forecast horizon is seven days. The adapter must convert documented units explicitly and select a
representative resort coordinate/elevation, rather than silently treating a coarse model grid as a
resort measurement. Past modeled conditions remain modeled, not station observations.
[Open-Meteo forecast documentation](https://open-meteo.com/en/docs).

Its ensemble API exposes individual forecast members. An eventual ensemble fraction exceeding a
declared snowfall threshold can provide a model-based uncertainty signal, but that alone is not a
calibrated probability of finding accessible, untracked powder. The initial app may use a documented
heuristic or unknown value instead, labelled accurately. A future validation set should compare
predicted events with observed outcomes.
[Open-Meteo ensemble documentation](https://open-meteo.com/en/docs/ensemble-api).

The hosted free Open-Meteo service is restricted to noncommercial use and rate limits. Advertising
or a commercial product requires an appropriate service arrangement; no subscription is activated
for this experiment. Data attribution is required. Cache/batch destination requests rather than
issuing a live request for every location on each page view.
[Open-Meteo terms](https://open-meteo.com/en/terms).

OpenStreetMap provides a candidate catalogue source: mapped `landuse=winter_sports` areas identify
winter-sports land, and Overpass supports queries against OSM data. Coverage, duplicate areas,
resort identity, coordinates and operating status still need validation; the tag is not a complete
verified resort directory. For larger refreshes prefer a maintained extract/import process over
repeating a global public-API query per user search.
[Winter-sports tagging](https://wiki.openstreetmap.org/wiki/Tag:landuse=winter_sports),
[Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API).

OSM data reuse requires attribution and compliance with ODbL conditions, including applicable
share-alike requirements when distributing an adapted database. Keep dataset provenance separate
from the application's source-code license.
[OpenStreetMap copyright and licensing](https://www.openstreetmap.org/copyright).

Real flight/hotel offers need a provider arrangement and freshness/availability handling. For
example, Amadeus distinguishes limited cached test data from production realtime data. Its sandbox
does not establish a currently bookable offer. The first experiment uses clearly labelled dated
price estimates; optional real-provider integration can follow without making up quotes or spending
money on accounts.
[Amadeus test-data documentation](https://amadeus4dev.github.io/developer-guides/test-data/).

## Milestones

1. **Foundation:** working frontend/backend, database catalogue/import, offline search/detail and
   seven-day synthetic forecasts with truthful provenance.
2. **Decisions:** all sorts/filters, currencies/duration-sensitive costs, useful geographic overview
   and responsive detail/results interface.
3. **Resilience:** optional actual forecast HTTP adapter, source times, failure/cache/missing-data
   handling, provider-stub tests and documented live/demo modes.
4. **Subsequent change:** independent accommodation nights and a travel-time filter, preserving
   existing clients and the transport/lift-price contracts.

Later product work includes verified large-scale catalogue ingestion, forecast calibration, actual
travel availability/pricing, richer globe interaction, user accounts/alerts, affiliate attribution and
advertising. These are product directions, not claims that the first experimental pair implements them.

## Success criteria

The website must run locally through uv, work offline, pass shared HTTP checks and its own meaningful
tests, expose honest data/cost states and remain usable on desktop/mobile. The later maintenance
task must preserve existing behavior. An independent review assesses responsibility, interface
clarity, coupling, duplication and the effort of the next change. Fewer lines or more named design
patterns do not by themselves establish a better implementation.
