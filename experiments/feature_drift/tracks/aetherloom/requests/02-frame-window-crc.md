# Request 2 — Lossy Frame Window & Sliding CRC-16 Replay Buffer

Add a sliding window protocol buffer that reorders out-of-sequence sub-tick frames, drops corrupted CRC frames, and requests selective NACK retransmissions.

Real transducers sit on noisy links: frames arrive late, twice, out of order or damaged. They
can't speak HTTP+JSON either; they send raw wire frames.

## What I need

`POST /api/fields/{id}/transducers/{tid}/wire` with a binary body (`application/octet-stream`)
containing one wire frame. Unknown field or transducer → `404 not_found`; otherwise always
`200` with a **receipt** (below), whatever happened to the frame.

### Wire frame (all integers big-endian)

| offset | size | field |
| --- | --- | --- |
| 0 | 2 | magic: ASCII `AL` |
| 2 | 1 | dialect id — `0` is the JSON dialect |
| 3 | 1 | dialect version, major |
| 4 | 1 | dialect version, minor |
| 5 | 4 | `seq`, unsigned |
| 9 | 2 | payload length `L`, unsigned |
| 11 | L | payload |
| 11+L | 2 | CRC over bytes `0 .. 10+L` |

The frame is exactly `13 + L` bytes. The CRC is **CRC-16/CCITT-FALSE**: polynomial `0x1021`,
initial value `0xFFFF`, no input/output reflection, final XOR `0x0000` (check value: the ASCII
bytes `123456789` give `0x29B1`).

JSON dialect version 1.0 payload: UTF-8 JSON `{"deltas": [{"q": 0, "r": 0, "delta": 0.5}, ...]}`,
the same shape and rules as the frames endpoint.

### Processing, in this order

1. **corrupt**: wrong magic, wrong total length, or CRC mismatch. Dropped; nothing else changes.
2. **rejected** (with an `error` code), and the frame does not touch the window:
   - `unsupported_dialect`: anything other than dialect `0` version `1.0`;
   - `invalid_payload`: the payload does not decode to the shape above;
   - `foreign_cell` / `conservation_violation`: as for the frames endpoint.
3. The **receive window** of the transducer: `next_seq` starts at `0`, the window size is `8`,
   so it accepts `next_seq <= seq < next_seq + 8`.
   - `duplicate`: `seq < next_seq`, or that `seq` is already buffered. Dropped.
   - `out_of_window`: `seq >= next_seq + 8`. Dropped.
   - `delivered`: `seq == next_seq`. The frame is delivered, then every buffered frame whose
     `seq` continues the run; `next_seq` moves past the last one delivered.
   - `buffered`: otherwise; held until the gap before it is filled.

Delivered frames join the field's frame queue (the same queue the JSON frames endpoint uses),
in delivery order, and are applied at the next Emit like any other frame.

### Receipt and window state

```json
{"status": "delivered", "seq": 4, "delivered": [4, 5, 6], "next_seq": 7, "nacks": [8]}
```

- `status` is one of `corrupt`, `rejected`, `duplicate`, `out_of_window`, `buffered`,
  `delivered`; `rejected` receipts also carry `"error": "<code>"`.
- `seq` is the frame's seq, or `null` when corrupt.
- `delivered` lists the seqs delivered by this frame in delivery order (empty unless
  `delivered`).
- `nacks` is the selective NACK list: every seq from `next_seq` up to (not including) the
  highest buffered seq that is not buffered, ascending; empty when nothing is buffered.

`GET /api/fields/{id}/transducers/{tid}/window` →

```json
{"next_seq": 7, "buffered": [9], "nacks": [7, 8],
 "stats": {"received": 12, "delivered": 7, "buffered": 3, "duplicate": 1, "out_of_window": 0, "corrupt": 2, "rejected": 1}}
```

`stats` counts wire frames received, frames delivered (including ones released from the
buffer), and receipts by status (`buffered`, `duplicate`, `out_of_window`, `corrupt`,
`rejected`).
