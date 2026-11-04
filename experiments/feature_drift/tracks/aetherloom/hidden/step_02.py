"""Request 2: wire frames, CRC-16/CCITT-FALSE, receive window and selective NACKs."""

import struct

from conftest import DEFAULT_BOUNDARY, assert_cells, assert_error, lm


def setup(app, radius=2):
    fid = app.field(radius=radius, k=0.0625)
    app.transducer(fid, "t1", [(0, 0), (1, 0), (0, 1)])
    app.transducer(fid, "t2", [(-1, 0)])
    return fid


def deltas_for(seq: int):
    a = (seq + 1) * 0.1
    return [((0, 0), a), ((1, 0), -a / 3), ((0, 1), -(a - a / 3))]


def test_valid_frame_is_delivered_and_applied(app):
    fid = setup(app)
    model = lm.Field(2, k=0.0625, boundary=DEFAULT_BOUNDARY)
    deltas = [((0, 0), 2.5), ((1, 0), -2.0), ((0, 1), -0.5)]
    receipt = app.wire(fid, "t1", lm.frame(0, deltas))
    assert receipt == {
        "status": "delivered",
        "seq": 0,
        "delivered": [0],
        "next_seq": 1,
        "nacks": [],
    }
    model.queue = [deltas]
    app.step(fid)
    model.step()
    assert_cells(app.cells(fid), model.v)
    assert_error(app.post("/api/fields/nope/transducers/t1/wire", content=b"AL"), 404, "not_found")
    assert_error(
        app.post(f"/api/fields/{fid}/transducers/zz/wire", content=b"AL"), 404, "not_found"
    )


def test_reordering_and_selective_nacks(app):
    fid = setup(app)
    model = lm.Field(2, k=0.0625, boundary=DEFAULT_BOUNDARY)
    r = app.wire(fid, "t1", lm.frame(3, deltas_for(3)))
    assert r == {"status": "buffered", "seq": 3, "delivered": [], "next_seq": 0, "nacks": [0, 1, 2]}
    r = app.wire(fid, "t1", lm.frame(1, deltas_for(1)))
    assert (r["status"], r["nacks"]) == ("buffered", [0, 2])
    r = app.wire(fid, "t1", lm.frame(0, deltas_for(0)))
    assert r == {"status": "delivered", "seq": 0, "delivered": [0, 1], "next_seq": 2, "nacks": [2]}
    window = app.get(f"/api/fields/{fid}/transducers/t1/window").json()
    assert (window["next_seq"], window["buffered"], window["nacks"]) == (2, [3], [2])
    r = app.wire(fid, "t1", lm.frame(2, deltas_for(2)))
    assert r == {"status": "delivered", "seq": 2, "delivered": [2, 3], "next_seq": 4, "nacks": []}
    model.queue = [deltas_for(s) for s in (0, 1, 2, 3)]
    app.step(fid)
    model.step()
    assert_cells(app.cells(fid), model.v)
    stats = app.get(f"/api/fields/{fid}/transducers/t1/window").json()["stats"]
    assert stats["received"] == 4 and stats["delivered"] == 4 and stats["buffered"] == 2


def test_corrupt_frames_are_dropped(app):
    fid = setup(app)
    good = lm.frame(0, deltas_for(0))
    flipped = good[:14] + bytes([good[14] ^ 0x01]) + good[15:]
    bad_crc = good[:-2] + struct.pack(">H", (lm.crc16(good[:-2]) + 1) & 0xFFFF)
    body = good[2:-2]
    wrong_magic = b"XL" + body + struct.pack(">H", lm.crc16(b"XL" + body))
    for data in (flipped, bad_crc, good[:-1], good + b"\x00", wrong_magic, b"", b"AL\x00"):
        r = app.wire(fid, "t1", data)
        assert r == {"status": "corrupt", "seq": None, "delivered": [], "next_seq": 0, "nacks": []}
    window = app.get(f"/api/fields/{fid}/transducers/t1/window").json()
    assert window["stats"]["corrupt"] == 7 and window["next_seq"] == 0
    assert app.wire(fid, "t1", good)["status"] == "delivered"


def test_duplicates_and_window_bounds(app):
    fid = setup(app)
    assert app.wire(fid, "t1", lm.frame(0, deltas_for(0)))["status"] == "delivered"
    r = app.wire(fid, "t1", lm.frame(0, deltas_for(0)))
    assert (r["status"], r["seq"], r["next_seq"]) == ("duplicate", 0, 1)
    assert app.wire(fid, "t1", lm.frame(9, deltas_for(9)))["status"] == "out_of_window"
    r = app.wire(fid, "t1", lm.frame(8, deltas_for(8)))
    assert (r["status"], r["nacks"]) == ("buffered", [1, 2, 3, 4, 5, 6, 7])
    assert app.wire(fid, "t1", lm.frame(8, deltas_for(8)))["status"] == "duplicate"
    stats = app.get(f"/api/fields/{fid}/transducers/t1/window").json()["stats"]
    assert (stats["duplicate"], stats["out_of_window"]) == (2, 1)
    other = app.get(f"/api/fields/{fid}/transducers/t2/window").json()
    assert (other["next_seq"], other["buffered"], other["nacks"]) == (0, [], [])


def test_rejected_frames_do_not_touch_the_window(app):
    fid = setup(app)
    cases = [
        (lm.envelope(0, lm.json_payload(deltas_for(0)), dialect=200), "unsupported_dialect"),
        (lm.envelope(0, b'{"deltas": "nope"}'), "invalid_payload"),
        (lm.envelope(0, b"\xff\xfe not json"), "invalid_payload"),
        (lm.frame(0, [((0, 0), 1.0), ((-1, 0), -1.0)]), "foreign_cell"),
        (lm.frame(0, [((0, 0), 1.0), ((1, 0), -0.75)]), "conservation_violation"),
    ]
    for data, code in cases:
        r = app.wire(fid, "t1", data)
        assert r == {
            "status": "rejected",
            "error": code,
            "seq": 0,
            "delivered": [],
            "next_seq": 0,
            "nacks": [],
        }, (code, r)
    assert app.get(f"/api/fields/{fid}/transducers/t1/window").json()["stats"]["rejected"] == 5
    assert app.wire(fid, "t1", lm.frame(0, deltas_for(0)))["status"] == "delivered"
