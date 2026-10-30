# Request — Per-integration timeouts

Five seconds is too long for some providers and too short for others.

- Setting `timeouts`: integration → seconds (number > 0), default `{}`; unlisted integrations
  keep the 5-second default. Unknown integration names or invalid values →
  `422 invalid_request`.
- A timed-out provider request now gets audit `"outcome": "timeout"` (instead of `error`), still
  with `status: null`, and the operation still returns `504 upstream_timeout`.
