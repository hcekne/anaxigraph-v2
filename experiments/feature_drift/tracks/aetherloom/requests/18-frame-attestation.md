# Request 18 — Cryptographic Frame Attestation & Replay-Window Watermarks

Verify per-transducer Ed25519/HMAC frame signatures and reject replayed frames even across checkpoint restores.

A penetration test showed that anyone on a link can inject frames, and that a captured frame
can simply be sent again — even after we rewind a field. Devices now sign their frames. (Our
locked environment has no Ed25519 library, so we use HMAC-SHA256 with per-device keys.)

## What I need

### Keys

`PUT /api/fields/{id}/transducers/{tid}/key` with `{"key": "<base64>"}` (16–64 bytes once
decoded; otherwise `422 invalid_request`) → `204`. Setting it again rotates the key. The key is
never returned anywhere. The transducer object gains `"attested"` (whether it has a key) and
`"watermark"` (the highest counter accepted so far, or `null`).

### Signed frames

Once a transducer has a key, every wire frame it sends — to `/wire` or through a link — must be
followed by a 40-byte **attestation trailer**:

| size | field |
| --- | --- |
| 8 | `counter`, unsigned big-endian |
| 32 | `mac` = HMAC-SHA256(key, frame bytes ‖ counter bytes) |

"Frame bytes" are the whole wire frame of request 2 (header, payload and CRC). The body is then
exactly `13 + L + 40` bytes. For such a transducer, wire processing starts like this:

1. `corrupt`: the frame part fails the request 2 checks, or the total length is not
   `13 + L + 40`.
2. `rejected` / `quarantined`, as before.
3. `rejected` / `bad_signature`: the MAC does not match.
4. `rejected` / `replayed`: the counter was seen before (see below).
5. Then everything else as before (dialect, payload, conservation, window, …).

Transducers without a key keep working exactly as before.

### Replay window and watermarks

Each keyed transducer keeps a replay window over counters: the **watermark** `H` (highest
accepted counter) and which counters in `(H - 64, H]` were accepted. A counter `<= H - 64`, or
one already accepted, is `replayed`. Counters may otherwise arrive out of order. A counter is
accepted as soon as its MAC checks out and it is fresh — even if the frame is then rejected for
another reason or dropped by the receive window.

Watermarks and replay windows are **not** part of checkpoints: restoring a field never rewinds
them, so a frame captured before a restore cannot be replayed after it. They survive restarts
and key rotation.
