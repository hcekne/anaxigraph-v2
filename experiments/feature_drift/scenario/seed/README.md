# Switchboard

Switchboard connects a small business to the outside services it already pays for (weather,
CRM, calendars, accounting, internal tools) and exposes them through one HTTP API to people,
internal dashboards and an AI assistant. It is early: only the skeleton exists. Product
requests arrive one at a time in `docs/requests/`.

## Run

```sh
uv sync --locked
.venv/bin/python -m switchboard serve --host 127.0.0.1 --port 8000
.venv/bin/python -m pytest
```

`run.json` holds the start command with a `{port}` placeholder; deployment and acceptance
tooling launch the service from it, so keep it working.

## Environment

| Variable | Meaning |
| --- | --- |
| `SWITCHBOARD_EXTERNAL_URL` | Base URL in front of every third-party provider. Provider docs give paths relative to it, for example `{EXTERNAL}/weatherco/v1/geocode`. |
| `SWITCHBOARD_DATA_DIR` | Writable directory for anything the service must keep. |

There is no other configuration channel: credentials arrive through the API.

## API conventions

- JSON in and out. Dates are `YYYY-MM-DD`; timestamps are ISO-8601 UTC.
- Errors use one envelope: `{"error": {"code": "<stable_code>", "message": "<text>"}}`
  (see `switchboard/errors.py`). Requests name the codes they expect. Unless a request
  says otherwise:
  - `invalid_request` (422): missing or invalid parameters.
  - `not_found` (404): unknown resource.
  - `not_configured` (409): the needed provider has no credentials yet.
  - `upstream_auth` (502): a provider rejected our credentials.
  - `upstream_error` (502): a provider failed or returned something unusable.
- Credentials are write-only: no endpoint ever returns them.

## Development

The development sandbox has no network access. Tests must never call real providers:
fake them in-process (for example with `httpx.MockTransport` or FastAPI's `TestClient`).
Do not add third-party dependencies; the locked environment is all there is.
