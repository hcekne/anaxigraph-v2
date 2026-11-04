# AetherLoom

AetherLoom is a deterministic simulation engine for a hexagonal field: a grid of hex cells
that each hold a quantity which flows to neighbouring cells tick by tick. Field devices
("transducers") push framed telemetry into the field, and operators inspect, audit and replay
it through one HTTP API. Results must be exactly reproducible: the same inputs always give
bit-identical numbers. It is early: only the skeleton exists. Product requests arrive one at
a time in `docs/requests/`.

## Run

```sh
uv sync --locked
.venv/bin/python -m aetherloom serve --host 127.0.0.1 --port 8000
.venv/bin/python -m pytest
```

`run.json` holds the start command with a `{port}` placeholder; deployment and acceptance
tooling launch the service from it, so keep it working.

## Environment

| Variable | Meaning |
| --- | --- |
| `AETHERLOOM_DATA_DIR` | Writable directory for anything the service must keep. |

There is no other configuration channel: everything else arrives through the API.

## API conventions

- JSON in and out unless a request says a body is binary (`application/octet-stream`).
  Binary data inside JSON is standard base64 with padding.
- Numbers are JSON numbers. Floating-point values are IEEE-754 doubles and must survive the
  JSON round trip exactly (Python's `json` module does this).
- Errors use one envelope: `{"error": {"code": "<stable_code>", "message": "<text>"}}`
  (see `aetherloom/errors.py`). Requests name the codes they expect. Unless a request
  says otherwise:
  - `invalid_request` (422): missing or invalid parameters (wrong type, out of range,
    malformed JSON).
  - `not_found` (404): unknown resource.
  - `conflict` (409): the resource already exists.
- Identifiers chosen by clients match `[a-z0-9-]{1,32}` unless a request says otherwise.
- Secrets (keys) are write-only: no endpoint ever returns them.

## Development

The development sandbox has no network access. Do not add third-party dependencies; the
locked environment (FastAPI, uvicorn, httpx, pytest) plus the Python standard library is
all there is.
