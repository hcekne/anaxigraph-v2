"""Evaluator-owned model of the public AetherLoom contracts (independent of any candidate).

Everything here follows the request texts: hex geometry, the tick rule with its exact
floating-point operation order, wire framing, CRC, dialect payloads and link fragments.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import struct

DIRS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]


def ring(q: int, r: int) -> int:
    return max(abs(q), abs(r), abs(q + r))


def grid(radius: int) -> list[tuple[int, int]]:
    return sorted(
        (q, r)
        for q in range(-radius, radius + 1)
        for r in range(-radius, radius + 1)
        if ring(q, r) <= radius
    )


class Field:
    """Single- or multi-plane field stepped tick by tick exactly as specified."""

    def __init__(self, radius, k=0.1, boundary="absorbing", planes=None, coupling=None):
        self.radius = radius
        self.cells = grid(radius)
        self.inside = set(self.cells)
        self.planes = planes or [("scalar", k)]
        n = len(self.planes)
        self.coupling = coupling or [[0.0] * n for _ in range(n)]
        self.boundary = boundary
        self.values = [{c: 0.0 for c in self.cells} for _ in range(n)]
        self.sinks = [0.0] * n
        self.tick = 0
        self.sub_tick = 0
        self.expected = 0.0
        self.queue: list[list[tuple[tuple[int, int], float]]] = []
        self.bridges: dict[str, tuple[int, float]] = {}
        self.residuals: dict[str, dict] = {}
        self.gate = None  # callable(field, cell) -> gate in [0, 1]
        self.arbitrated = None
        self.on_boundary = None  # callable(field) run at a tick boundary, before frames
        self.last_flows: dict = {}

    @property
    def v(self) -> dict:
        return self.values[0]

    def set(self, values: dict, plane: int = 0) -> None:
        for cell, value in values.items():
            self.expected = self.expected + (value - self.values[plane][cell])
            self.values[plane][cell] = value

    def total(self) -> float:
        total = 0.0
        for c in self.cells:
            for p in range(len(self.planes)):
                total = total + self.values[p][c]
        return total

    def sink(self) -> float:
        total = 0.0
        for s in self.sinks:
            total = total + s
        return total

    def emit(self) -> None:
        for frame in self.queue:
            owner = None
            if isinstance(frame, tuple):
                owner, frame = frame
            for cell, delta in frame:
                applied = delta
                if owner in self.bridges:
                    bits, step = self.bridges[owner]
                    res = self.residuals.setdefault(owner, {})
                    x = delta + res.get(cell, 0.0)
                    n = round(x / step)
                    n = max(-(2 ** (bits - 1)), min(2 ** (bits - 1) - 1, n))
                    applied = n * step
                    res[cell] = x - applied
                self.values[0][cell] = self.values[0][cell] + applied
                self.expected = self.expected + delta
        self.queue = []

    def residual_total(self) -> float:
        total = 0.0
        for tid in sorted(self.residuals):
            for cell in sorted(self.residuals[tid]):
                total = total + self.residuals[tid][cell]
        return total

    def gates(self) -> dict | None:
        if self.gate is None:
            return None
        return {c: self.gate(self, c) for c in self.cells}

    def commit(self, gates=None) -> None:
        """Propagate + Arbitrate + Commit on the current (post-Emit) values."""
        snap = [dict(p) for p in self.values]
        n = len(self.planes)
        flows = {}
        for p, (_, k) in enumerate(self.planes):
            v = snap[p]
            new = {}
            for c in self.cells:
                acc = v[c]
                for d, (dq, dr) in enumerate(DIRS):
                    nb = (c[0] + dq, c[1] + dr)
                    if nb in self.inside:
                        f = k * (v[c] - v[nb])
                        if gates is not None:
                            f = f * min(gates[c], gates[nb])
                        acc = acc - f
                        flows[(p, c, nb)] = f
                    else:
                        f = k * v[c]
                        if gates is not None:
                            f = f * gates[c]
                        if self.boundary == "absorbing":
                            acc = acc - f
                        else:
                            m = (c[0] - dq, c[1] - dr)
                            if m in self.inside:
                                acc = acc - f
                                flows[(p, c, m, "reflect")] = f
                if self.boundary == "reflective":
                    for dq, dr in DIRS:
                        s = (c[0] + dq, c[1] + dr)
                        if s in self.inside and (s[0] + dq, s[1] + dr) not in self.inside:
                            f = k * v[s]
                            if gates is not None:
                                f = f * gates[s]
                            acc = acc + f
                for j in range(n):
                    if j != p:
                        acc = acc - self.coupling[p][j] * v[c]
                for j in range(n):
                    if j != p:
                        acc = acc + self.coupling[j][p] * snap[j][c]
                new[c] = acc
            if self.boundary == "absorbing":
                sink = self.sinks[p]
                for c in self.cells:
                    for dq, dr in DIRS:
                        if (c[0] + dq, c[1] + dr) not in self.inside:
                            f = k * v[c]
                            if gates is not None:
                                f = f * gates[c]
                            sink = sink + f
                self.sinks[p] = sink
            self.values[p] = new
        self.last_flows = flows
        self.tick += 1

    def step(self, ticks: int = 1) -> None:
        self.run(4 * ticks)

    def run(self, sub_ticks: int) -> None:
        """Run micro-phases: 0 Emit, 1 Propagate, 2 Arbitrate, 3 Commit."""
        for _ in range(sub_ticks):
            if self.sub_tick == 0:
                if self.on_boundary is not None:
                    self.on_boundary(self)
                self.emit()
            elif self.sub_tick == 2:
                self.arbitrated = self.gates()
            elif self.sub_tick == 3:
                self.commit(self.arbitrated)
                self.sub_tick = -1
            self.sub_tick += 1

    def audit(self) -> dict:
        cells, sink = self.total(), self.sink()
        return {
            "cells_total": cells,
            "sink_total": sink,
            "expected_total": self.expected,
            "residual_total": self.residual_total(),
        }


# -- CRC and wire envelope (request 2) -------------------------------------------------------


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, xorout 0."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


assert crc16(b"123456789") == 0x29B1

DIALECT_IDS = {"json": 0, "compactbin": 1, "lineproto": 2, "cborlite": 3, "deltaframe": 4}


def envelope(seq: int, payload: bytes, dialect: int = 0, version=(1, 0)) -> bytes:
    head = b"AL" + struct.pack(">BBBIH", dialect, version[0], version[1], seq, len(payload))
    body = head + payload
    return body + struct.pack(">H", crc16(body))


def json_payload(deltas, version="1.0", note=None) -> bytes:
    if version == "2.0":
        obj = {"d": [[q, r, d] for (q, r), d in deltas]}
    else:
        obj = {"deltas": [{"q": q, "r": r, "delta": d} for (q, r), d in deltas]}
        if note is not None:
            obj["note"] = note
    return json.dumps(obj).encode()


def frame(seq, deltas, dialect="json", version="1.0") -> bytes:
    major, minor = (int(x) for x in version.split("."))
    return envelope(seq, encode(dialect, deltas, version), DIALECT_IDS[dialect], (major, minor))


# -- Dialect payloads (request 6) ------------------------------------------------------------


def _cbor_head(major: int, value: int) -> bytes:
    if value < 24:
        return bytes([major << 5 | value])
    if value < 256:
        return bytes([major << 5 | 24, value])
    if value < 65536:
        return bytes([major << 5 | 25]) + struct.pack(">H", value)
    return bytes([major << 5 | 26]) + struct.pack(">I", value)


def _cbor_int(value: int) -> bytes:
    return _cbor_head(0, value) if value >= 0 else _cbor_head(1, -1 - value)


def encode(dialect: str, deltas, version: str = "1.0") -> bytes:
    if dialect == "json":
        return json_payload(deltas, version)
    if dialect == "compactbin":
        out = struct.pack(">H", len(deltas))
        for (q, r), d in deltas:
            out += struct.pack(">bbd", q, r, d)
        return out
    if dialect == "lineproto":
        return "".join(f"cell,q={q},r={r} delta={d!r}\n" for (q, r), d in deltas).encode()
    if dialect == "cborlite":
        out = _cbor_head(4, len(deltas))
        for (q, r), d in deltas:
            out += b"\x83" + _cbor_int(q) + _cbor_int(r) + b"\xfb" + struct.pack(">d", d)
        return out
    if dialect == "deltaframe":
        out = struct.pack(">H", len(deltas))
        prev = (0, 0)
        for (q, r), d in deltas:
            out += struct.pack(">bbd", q - prev[0], r - prev[1], d)
            prev = (q, r)
        return out
    raise ValueError(dialect)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# -- Policy expressions (request 4): static costs -------------------------------------------


def isfinite(x: float) -> bool:
    return math.isfinite(x)


# -- Attestation (request 18) ----------------------------------------------------------------


def attest(frame_bytes: bytes, key: bytes, counter: int) -> bytes:
    trailer = struct.pack(">Q", counter)
    mac = hmac.new(key, frame_bytes + trailer, hashlib.sha256).digest()
    return frame_bytes + trailer + mac


# -- Sectors (request 15) --------------------------------------------------------------------


def sector_of(cell, size: int) -> tuple[int, int]:
    return (cell[0] // size, cell[1] // size)


def sectors(radius: int, size: int) -> dict:
    """(sq, sr) -> {"cells": [...], "halo": set()} for the non-empty sectors."""
    inside = set(grid(radius))
    found: dict = {}
    for cell in grid(radius):
        found.setdefault(sector_of(cell, size), {"cells": [], "halo": set()})["cells"].append(cell)
    for key, row in found.items():
        for c in row["cells"]:
            for dq, dr in DIRS:
                n = (c[0] + dq, c[1] + dr)
                if n in inside and sector_of(n, size) != key:
                    row["halo"].add(n)
    return dict(sorted(found.items()))


def exchange(model: Field, size: int) -> dict:
    """Net inflow per sector during the model's last commit."""
    result: dict = {}
    for key, flow in model.last_flows.items():
        source, target = key[1], key[2]
        if len(key) == 3 and target < source:
            continue  # the same edge seen from the other side
        a, b = sector_of(source, size), sector_of(target, size)
        if a != b:
            result[b] = result.get(b, 0.0) + flow
            result[a] = result.get(a, 0.0) - flow
    return result


# -- Link fragmentation (request 17) ---------------------------------------------------------


def chunks(data: bytes, size: int) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)]


def lora_fragments(data: bytes, msg_id: int) -> list[bytes]:
    parts = chunks(data, 48)
    return [bytes([msg_id, i, len(parts)]) + p for i, p in enumerate(parts)]


def acoustic_fragments(data: bytes, msg_id: int) -> list[bytes]:
    parts = chunks(data, 29)
    out = []
    for i, p in enumerate(parts):
        seq = i | (0x80 if i == len(parts) - 1 else 0)
        out.append(struct.pack(">HB", msg_id, seq) + p)
    return out


def optical_fragments(data: bytes, msg_id: int, size: int = 249) -> list[bytes]:
    return [
        struct.pack(">HHH", msg_id, off, len(data)) + data[off : off + size]
        for off in range(0, len(data), size)
    ]


def can_fragments(data: bytes) -> list[bytes]:
    if len(data) <= 7:
        return [bytes([len(data)]) + data]
    out = [bytes([0x10 | (len(data) >> 8), len(data) & 0xFF]) + data[:6]]
    rest, n = data[6:], 1
    while rest:
        out.append(bytes([0x20 | (n & 0xF)]) + rest[:7])
        rest, n = rest[7:], n + 1
    return out


def flush_residuals(model: Field, tid: str) -> None:
    """Request 20: a quarantined bridged transducer's residuals go back into its cells."""
    residuals = model.residuals.get(tid, {})
    for cell in sorted(residuals):
        if residuals[cell] != 0.0:
            model.values[0][cell] = model.values[0][cell] + residuals[cell]
            residuals[cell] = 0.0
