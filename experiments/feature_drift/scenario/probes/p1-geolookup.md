# Request — GeoLookup

A customer wants addresses turned into coordinates. Add the GeoLookup integration. It must
behave like every other integration Switchboard has (credentials, overview and health,
capabilities, workspaces, audit with redaction, quotas at 1 unit, caching of reads, switching
off and credential removal, circuit breaking and timeouts).

`PUT /api/credentials/geo` `{"api_key": "..."}`. Overview kind `rest`.
Capability `geo.lookup`, input `address`* string → `{"address": ..., "lat": 59.91, "lon": 10.75, "label": "Oslo"}`
using the first match. No match → `404 not_found`.

API: `GET {EXTERNAL}/geo/v1/search?address=<text>` with header `X-Geo-Key: <api_key>` →
`{"matches": [{"lat": 59.91, "lng": 10.75, "label": "Oslo"}]}`. Bad key → `401`.
