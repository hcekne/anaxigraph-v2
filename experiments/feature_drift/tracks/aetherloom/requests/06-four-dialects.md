# Request 6 — Four Telemetry Transducer Dialects (CompactBin, LineProtocol, CBORLite, DeltaFrame)

Implement four wire-format transducer dialects that encode and decode sub-tick cell deltas.

The next batch of devices can't afford JSON. We support four compact formats from now on, and
our device teams want an endpoint to check their encoders against ours.

## What I need

### The dialects

All four have the single version `1.0`, take part in the handshake and in `GET /api/dialects`
like `json`, and carry a non-empty list of deltas `(q, r, delta)`. Integers are big-endian;
`f64` is an IEEE-754 binary64, big-endian. A payload that does not match its format exactly
(wrong length, trailing bytes, empty list, non-finite delta, …) is an `invalid_payload`.

| name | id | payload |
| --- | --- | --- |
| `compactbin` | 1 | `count: u16`, then `count` × (`q: i8`, `r: i8`, `delta: f64`) |
| `lineproto` | 2 | UTF-8 text, one line per delta: `cell,q=<q>,r=<r> delta=<delta>\n` |
| `cborlite` | 3 | a CBOR subset, below |
| `deltaframe` | 4 | `count: u16`, then `count` × (`dq: i8`, `dr: i8`, `delta: f64`) |

- **lineproto**: every line, including the last, ends with `\n`; no blank lines or extra
  spaces. `q` and `r` are decimal integers (`-?[0-9]+`). The encoder writes `delta` as the
  shortest decimal that round-trips (Python's `repr(float)`, e.g. `0.1`, `-2.5`, `1e-05`); the
  decoder accepts `-?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][-+]?[0-9]+)?`.
- **cborlite** ([RFC 8949](https://www.rfc-editor.org/rfc/rfc8949) subset): the payload is one
  definite-length array (major type 4) of entries; each entry is a definite-length array of
  exactly three items: `q` and `r` as integers (major type 0 for `n >= 0`, major type 1 holding
  `-1 - n` for `n < 0`) and `delta` as a float64 (`0xFB` + 8 bytes). Lengths and integer
  arguments use additional info 0–23 (inline), 24 (+1 byte), 25 (+2 bytes) or 26 (+4 bytes).
  The encoder always uses the shortest form; the decoder accepts any of these widths and
  rejects everything else (other major types, indefinite lengths, float16/float32, trailing
  bytes).
- **deltaframe**: `(dq, dr)` is the entry's `(q, r)` minus the previous entry's; the first
  entry is relative to `(0, 0)`.

Coordinates that do not fit the format (`i8`) cannot be encoded → `422 invalid_request`.

### Codec endpoints

- `POST /api/dialects/{name}/encode` with
  `{"version": "1.0", "deltas": [{"q": 0, "r": 1, "delta": 0.5}, ...]}` →
  `200 {"payload": "<base64>"}`. Deltas follow the frames endpoint rules
  (`422 invalid_request`). `json` encodes too (any bytes its decoder accepts).
- `POST /api/dialects/{name}/decode` with `{"version": "1.0", "payload": "<base64>"}` →
  `200 {"deltas": [{"q", "r", "delta"}, ...]}` in payload order. Bad base64 →
  `422 invalid_request`; undecodable payload → `422 invalid_payload`.
- `version` defaults to the dialect's highest version. Unknown dialect → `404 not_found`;
  version the dialect doesn't have → `422 unsupported_version`.

Wire frames in these dialects use their dialect id in the header and are accepted exactly like
JSON ones once a transducer has agreed on the dialect in a handshake.
