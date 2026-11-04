# Request 17 — Four Additional Edge Transducers (LoRaBurst, AcousticModem, OpticalLink, CANBus)

Add four constrained-MTU physical link transducers with custom frame fragmentation and reassembly.

Our newest devices sit behind radio, underwater acoustic, free-space optical and CAN links.
None of them can carry a whole wire frame at once: each link splits a frame into fragments
with its own small header. We receive the fragments one by one and must put the frame back
together before handling it like any other wire frame.

## What I need

`POST /api/fields/{id}/transducers/{tid}/links/{link}` with one fragment as a binary body.

- Unknown field/transducer/link → `404 not_found`. A fragment longer than the link's MTU →
  `422 mtu_exceeded`; a fragment whose header is malformed → `422 invalid_fragment`. Neither
  changes any state.
- Until a frame is complete the answer is `200 {"status": "fragment_buffered"}`; a fragment
  the reassembly already has → `{"status": "fragment_duplicate"}`; a fragment that breaks an
  ordered link's sequence → `{"status": "fragment_dropped"}` (see below).
- The fragment that completes a frame gets the **wire receipt** of that frame: the reassembled
  bytes are processed exactly as if posted to `/wire` (CRC, dialect, window, quarantine, …).
- Reassembly state is kept per transducer and link, and is cleared for a message once it
  completes.
- `GET /api/links` → `{"links": [{"name": "acoustic", "mtu": 32}, ...]}` sorted by name.

All integers are big-endian. "Data" is the next slice of the wire frame's bytes.

### `lora` — LoRaBurst, MTU 51

Header `msg_id: u8`, `index: u8`, `count: u8`, then 1–48 data bytes. Needs `count >= 1` and
`index < count` (else `invalid_fragment`). Fragments of a message (same `msg_id`) may arrive in
any order and interleaved with other messages; the frame is the data of indexes `0..count-1`
concatenated. A fragment whose `count` differs from the one already buffered for its `msg_id`
discards that partial message and starts over with this fragment.

### `acoustic` — AcousticModem, MTU 32

Header `msg_id: u16`, `seq: u8` (bit 7: last fragment; bits 0–6: fragment number), then 1–29
data bytes. Ordered: fragment number 0 always starts a new message (discarding any partial
one); otherwise the number must be the next one of the partial message with the same `msg_id`,
or the partial message is discarded and the answer is `fragment_dropped`. The fragment with
the last-fragment bit completes the frame.

### `optical` — OpticalLink, MTU 255

Header `msg_id: u16`, `offset: u16`, `total: u16`, then 1–249 data bytes, with `total >= 1`
and `offset + len(data) <= total` (else `invalid_fragment`). Fragments may arrive in any order
and may overlap (later bytes overwrite earlier ones); the frame is complete when every byte
`0..total-1` has been received. A different `total` for a buffered `msg_id` starts over. A
fragment that adds no new byte is a `fragment_duplicate`.

### `canbus` — CANBus, MTU 8

ISO-TP style (ISO 15765-2), one message at a time per transducer. The high nibble of byte 0
is the frame type:

- `0` single frame: low nibble `L` (1–7) is the length, followed by at least `L` bytes (extra
  padding ignored). Completes immediately.
- `1` first frame: 12-bit length `L` = low nibble of byte 0 and byte 1 (`8 <= L <= 4095`),
  then 6 data bytes. Starts a new message, discarding any partial one.
- `2` consecutive frame: low nibble is the sequence number — 1 for the first after the first
  frame, then 2, …, 15, 0, 1, … — followed by up to 7 data bytes (the final one may carry
  padding after the remaining bytes). A wrong number, or no message in progress →
  `fragment_dropped` (and any partial message is discarded).
- Any other type, or a frame too short for its type → `invalid_fragment`.
