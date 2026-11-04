"""Request 6: compactbin, lineproto, cborlite and deltaframe payloads plus codec endpoints."""

import base64
import struct

from conftest import DEFAULT_BOUNDARY, assert_cells, assert_error, lm

NEW = {"compactbin": 1, "lineproto": 2, "cborlite": 3, "deltaframe": 4}
SAMPLE = [((0, 1), 0.1), ((-25, 3), -2.5), ((30, -1), 1e-05), ((2, 2), 2.4)]


def encode(app, name, deltas, version="1.0"):
    body = {"version": version, "deltas": [{"q": q, "r": r, "delta": d} for (q, r), d in deltas]}
    return app.post(f"/api/dialects/{name}/encode", json=body)


def decode(app, name, payload: bytes, version="1.0"):
    body = {"version": version, "payload": base64.b64encode(payload).decode()}
    return app.post(f"/api/dialects/{name}/decode", json=body)


def as_rows(deltas):
    return [{"q": q, "r": r, "delta": d} for (q, r), d in deltas]


def test_listing(app):
    listed = {d["name"]: d for d in app.get("/api/dialects").json()["dialects"]}
    for name, ident in NEW.items():
        assert listed[name]["id"] == ident and listed[name]["versions"] == ["1.0"]


def test_exact_encodings_and_round_trips(app):
    for name in NEW:
        response = encode(app, name, SAMPLE)
        assert response.status_code == 200, (name, response.text)
        payload = base64.b64decode(response.json()["payload"])
        assert payload == lm.encode(name, SAMPLE), name
        decoded = decode(app, name, payload)
        assert decoded.status_code == 200 and decoded.json() == {"deltas": as_rows(SAMPLE)}, name
    response = encode(app, "json", SAMPLE, version="2.0")
    payload = base64.b64decode(response.json()["payload"])
    assert decode(app, "json", payload, version="2.0").json() == {"deltas": as_rows(SAMPLE)}
    assert lm.encode("lineproto", SAMPLE) == (
        b"cell,q=0,r=1 delta=0.1\ncell,q=-25,r=3 delta=-2.5\n"
        b"cell,q=30,r=-1 delta=1e-05\ncell,q=2,r=2 delta=2.4\n"
    )


def test_cbor_widths_and_long_lists(app):
    many = [((i % 20 - 10, (i * 7) % 30 - 15), i / 8 - 3.0) for i in range(300)]
    payload = base64.b64decode(encode(app, "cborlite", many).json()["payload"])
    assert payload[:3] == b"\x99\x01\x2c" and payload == lm.encode("cborlite", many)
    wide = b"\x81" + b"\x83" + b"\x18\x05" + b"\x39\x00\x63" + b"\xfb" + struct.pack(">d", 0.5)
    assert decode(app, "cborlite", wide).json() == {"deltas": [{"q": 5, "r": -100, "delta": 0.5}]}
    wider = b"\x81\x83\x1a\x00\x00\x00\x01\x00\xfb" + struct.pack(">d", -1.0)
    assert decode(app, "cborlite", wider).json() == {"deltas": [{"q": 1, "r": 0, "delta": -1.0}]}
    bad = [
        b"\x80",  # empty list
        b"\x81\x83\x01\x02\xfa" + struct.pack(">f", 0.5),  # float32
        b"\x81\x82\x01\x02",  # wrong arity
        b"\x9f\x83\x01\x02\xfb" + struct.pack(">d", 0.5) + b"\xff",  # indefinite length
        b"\x81\x83\x01\x02\xfb" + struct.pack(">d", 0.5) + b"\x00",  # trailing byte
        b"\x81\x83\x61\x61\x02\xfb" + struct.pack(">d", 0.5),  # text string
        b"\x81\x83\x01\x02\xfb" + struct.pack(">d", float("inf")),
    ]
    for payload in bad:
        assert_error(decode(app, "cborlite", payload), 422, "invalid_payload")


def test_binary_and_text_payload_errors(app):
    good = lm.encode("compactbin", SAMPLE)
    for payload in (good[:-1], good + b"\x00", struct.pack(">H", 0)):
        assert_error(decode(app, "compactbin", payload), 422, "invalid_payload")
    good = lm.encode("deltaframe", SAMPLE)
    assert_error(decode(app, "deltaframe", good[:-3]), 422, "invalid_payload")
    for text in (
        b"",
        b"cell,q=0,r=1 delta=0.5",
        b"cell,q=0,r=1 delta=0.5\n\n",
        b"cell,q=0,r=1  delta=0.5\n",
        b"cell,q=a,r=1 delta=0.5\n",
        b"cell,q=0,r=1 delta=nan\n",
        b"cell,q=0,r=1 delta=0.5\nxx\n",
    ):
        assert_error(decode(app, "lineproto", text), 422, "invalid_payload")
    assert decode(app, "lineproto", b"cell,q=-1,r=0 delta=.5e1\n").json() == {
        "deltas": [{"q": -1, "r": 0, "delta": 5.0}]
    }
    assert_error(
        app.post("/api/dialects/compactbin/decode", json={"payload": "@@@"}), 422, "invalid_request"
    )


def test_codec_endpoint_errors(app):
    assert_error(encode(app, "klingon", SAMPLE), 404, "not_found")
    assert_error(encode(app, "compactbin", SAMPLE, version="2.0"), 422, "unsupported_version")
    assert_error(encode(app, "compactbin", [((200, 0), 1.0)]), 422, "invalid_request")
    assert_error(encode(app, "compactbin", []), 422, "invalid_request")
    body = {"deltas": as_rows(SAMPLE)}
    response = app.post("/api/dialects/deltaframe/encode", json=body)
    assert base64.b64decode(response.json()["payload"]) == lm.encode("deltaframe", SAMPLE)


def test_wire_frames_in_every_dialect(app):
    fid = app.field(radius=3, k=0.125)
    model = lm.Field(3, k=0.125, boundary=DEFAULT_BOUNDARY)
    queue = []
    for i, name in enumerate(NEW):
        cells = [(i - 2, 0), (i - 2, 1), (i - 2, 2)]
        app.transducer(fid, name, cells)
        shake = {"dialect": name, "versions": ["1.0"], "capabilities": 3}
        assert (
            app.post(f"/api/fields/{fid}/transducers/{name}/handshake", json=shake).json()[
                "dialect"
            ]
            == name
        )
        deltas = [(cells[0], 1.5 + i), (cells[2], -0.5), (cells[1], -(1.0 + i))]
        assert app.wire(fid, name, lm.frame(0, deltas, dialect=name))["status"] == "delivered"
        assert (
            app.wire(fid, name, lm.frame(1, deltas, dialect="json"))["error"] == "dialect_mismatch"
        )
        queue.append(deltas)
    model.queue = queue
    app.step(fid, ticks=2)
    model.step(2)
    assert_cells(app.cells(fid), model.v)
