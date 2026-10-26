# Task 3 Real forecast boundary and honest failure behavior

Add an optional actual Open-Meteo forecast fetch/refresh path with timeout, bounded caching,
provider attribution and a documented way to enable it. Preserve offline operation. Provider
failure or missing snow data must result in an explicit stale/unknown/fallback state, never invented
current observations. Include source times and support repeatable provider-stub tests. Show a clear
loading/error state on the frontend. Clarify commercial data requirements and future affiliate/ad
extension points without activating paid services or biased rankings. Run all prior tests and commit.

Acceptance focus: provider stub success/failure, missing values, no credentials/network required by
tests, documented optional live command, source provenance and absence of misleading live prices.
