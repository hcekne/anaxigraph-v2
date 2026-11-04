# ChronoWeave

ChronoWeave is a bitemporal causal ledger. Every fact ("assertion") records when it is true
in the world (`effective_at`), when the ledger learned it (`asserted_at`) and which earlier
assertions it was derived from (`causal_parents`). Teams use it to answer "what did we believe,
as of then, about that moment?", to try out speculative changes on branches, and to prove to
auditors that history was not rewritten. It is early: only the skeleton exists. Product
requests arrive one at a time in `docs/requests/`.

## Run

```sh
uv sync --locked
.venv/bin/python -m chronoweave serve --host 127.0.0.1 --port 8000
.venv/bin/python -m pytest
```

`run.json` holds the start command with a `{port}` placeholder; deployment and acceptance
tooling launch the service from it, so keep it working.

## Environment

| Variable | Meaning |
| --- | --- |
| `CHRONOWEAVE_DATA_DIR` | Writable directory for anything the service must keep. |
| `CHRONOWEAVE_EXTERNAL_URL` | Base URL in front of every outside service ChronoWeave calls. Requests give paths relative to it, for example `{EXTERNAL}/authority/v1/...`. |

There is no other configuration channel: secrets and settings arrive through the API.

## API conventions

- JSON in and out unless a request says otherwise.
- **Times are integers** (seconds on a shared clock, think Unix seconds). One day is 86400.
- **Canonical JSON** means `json.dumps(value, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False)` encoded as UTF-8. Whenever a request hashes, signs or compares JSON it
  means canonical JSON.
- Hashes and MACs are lowercase hex strings.
- Errors use one envelope: `{"error": {"code": "<stable_code>", "message": "<text>"}}`
  (see `chronoweave/errors.py`). Requests name the codes they expect. Unless a request
  says otherwise:
  - `invalid_request` (422): missing, malformed or out-of-range parameters or body fields
    (including wrong JSON types, and booleans where integers are expected).
  - `not_found` (404): unknown resource.
  - `conflict` (409): the resource already exists or is in the wrong state.
  - `not_configured` (409): a needed setting or secret has not been provided yet.
  - `upstream_error` (502): an outside service failed or returned something unusable.
- Secrets are write-only: no endpoint ever returns them.

## Development

The development sandbox has no network access. Tests must never call real outside services:
fake them in-process (for example with `httpx.MockTransport` or FastAPI's `TestClient`).
Do not add third-party dependencies; the locked environment and the Python standard library
(including `sqlite3`, `hashlib`, `hmac`, `gzip`) are all there is.
