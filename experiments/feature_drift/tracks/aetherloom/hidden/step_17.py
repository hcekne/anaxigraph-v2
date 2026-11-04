"""Request 17: lora, acoustic, optical and canbus fragment reassembly into wire frames."""

import struct

from conftest import DEFAULT_BOUNDARY, assert_cells, assert_error, lm

LINKS = {"acoustic": 32, "canbus": 8, "lora": 51, "optical": 255}


def setup(app):
    fid = app.field(radius=3, k=0.125)
    cells = [(0, 0), (1, 0), (0, 1), (-1, 1), (2, -1)]
    app.transducer(fid, "t1", cells)
    return fid


def deltas(seq):
    """Enough deltas that the frame needs several fragments on every link."""
    a = 0.5 + seq
    return [
        ((0, 0), a),
        ((1, 0), -a / 4),
        ((0, 1), -a / 4),
        ((-1, 1), -a / 4),
        ((2, -1), -(a - 3 * (a / 4))),
    ]


def send(app, fid, link, fragment, tid="t1"):
    response = app.post(
        f"/api/fields/{fid}/transducers/{tid}/links/{link}",
        content=fragment,
        headers={"content-type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_catalogue_and_limits(app):
    body = app.get("/api/links").json()["links"]
    found = {row["name"]: row["mtu"] for row in body}
    assert {k: found.get(k) for k in LINKS} == LINKS
    assert [row["name"] for row in body] == sorted(found)
    fid = setup(app)
    for link, mtu in LINKS.items():
        response = app.post(
            f"/api/fields/{fid}/transducers/t1/links/{link}", content=b"\x00" * (mtu + 1)
        )
        assert_error(response, 422, "mtu_exceeded")
    assert_error(
        app.post(f"/api/fields/{fid}/transducers/t1/links/smoke", content=b"\x00"), 404, "not_found"
    )
    for link, fragment in (
        ("lora", b"\x01\x02\x02xx"),
        ("lora", b"\x01\x00\x00xx"),
        ("lora", b"\x01\x00"),
        ("acoustic", b"\x00\x01"),
        ("optical", struct.pack(">HHH", 1, 4, 5) + b"xx"),
        ("optical", struct.pack(">HHH", 1, 0, 0) + b"x"),
        ("canbus", b"\x30\x00"),
        ("canbus", b"\x05ab"),
        ("canbus", b"\x10\x05" + b"x" * 6),
    ):
        assert_error(
            app.post(f"/api/fields/{fid}/transducers/t1/links/{link}", content=fragment),
            422,
            "invalid_fragment",
        )


def test_every_link_reassembles_frames(app):
    fid = setup(app)
    model = lm.Field(3, k=0.125, boundary=DEFAULT_BOUNDARY)
    queue = []
    plans = [
        ("lora", lambda d: list(reversed(lm.lora_fragments(d, 7)))),
        ("acoustic", lambda d: lm.acoustic_fragments(d, 300)),
        ("optical", lambda d: [*reversed(lm.optical_fragments(d, 9, size=60))]),
        ("canbus", lm.can_fragments),
    ]
    for seq, (link, fragmenter) in enumerate(plans):
        data = lm.frame(seq, deltas(seq))
        parts = fragmenter(data)
        assert len(parts) >= 2
        for part in parts[:-1]:
            assert send(app, fid, link, part) == {"status": "fragment_buffered"}, link
        receipt = send(app, fid, link, parts[-1])
        assert receipt["status"] == "delivered" and receipt["delivered"] == [seq], (link, receipt)
        queue.append(deltas(seq))
    model.queue = queue
    app.step(fid)
    model.step()
    assert_cells(app.cells(fid), model.v)


def test_unordered_links_interleave_and_dedupe(app):
    fid = setup(app)
    a, b = lm.frame(0, deltas(0)), lm.frame(1, deltas(1))
    fa, fb = lm.lora_fragments(a, 1), lm.lora_fragments(b, 2)
    assert send(app, fid, "lora", fb[0])["status"] == "fragment_buffered"
    assert send(app, fid, "lora", fa[1])["status"] == "fragment_buffered"
    assert send(app, fid, "lora", fa[1])["status"] == "fragment_duplicate"
    for part in fb[1:]:
        last = send(app, fid, "lora", part)
    assert last["status"] == "buffered" and last["nacks"] == [0]
    for part in [p for i, p in enumerate(fa) if i != 1]:
        last = send(app, fid, "lora", part)
    assert last["delivered"] == [0, 1]
    data = lm.frame(2, deltas(2))
    parts = lm.optical_fragments(data, 5, size=40)
    overlap = struct.pack(">HHH", 5, 10, len(data)) + data[10:30]
    send(app, fid, "optical", parts[0])
    assert send(app, fid, "optical", overlap)["status"] == "fragment_duplicate"
    stale = struct.pack(">HHH", 5, 0, len(data) + 1) + b"\x00" * 10  # different total: restart
    send(app, fid, "optical", stale)
    for part in parts[1:]:
        assert send(app, fid, "optical", part)["status"] == "fragment_buffered"
    assert send(app, fid, "optical", parts[0])["delivered"] == [2]


def test_ordered_links_drop_broken_sequences(app):
    fid = setup(app)
    data = lm.frame(0, deltas(0))
    parts = lm.acoustic_fragments(data, 11)
    send(app, fid, "acoustic", parts[0])
    assert send(app, fid, "acoustic", parts[2])["status"] == "fragment_dropped"
    assert send(app, fid, "acoustic", parts[1])["status"] == "fragment_dropped"
    for part in parts[:-1]:
        assert send(app, fid, "acoustic", part)["status"] == "fragment_buffered"
    assert send(app, fid, "acoustic", parts[-1])["status"] == "delivered"
    can = lm.can_fragments(lm.frame(1, deltas(1)))
    assert send(app, fid, "canbus", can[2])["status"] == "fragment_dropped"
    send(app, fid, "canbus", can[0])
    send(app, fid, "canbus", can[1])
    assert send(app, fid, "canbus", can[3])["status"] == "fragment_dropped"
    for part in can[:-1]:
        assert send(app, fid, "canbus", part)["status"] == "fragment_buffered"
    assert send(app, fid, "canbus", can[-1] + b"\x00" * (8 - len(can[-1])))["delivered"] == [1]
    assert send(app, fid, "canbus", b"\x03ABC\x00\x00")["status"] == "corrupt"


def test_completed_frames_follow_wire_rules(app):
    fid = setup(app)
    data = lm.frame(0, [((0, 0), 1.0), ((1, 0), -0.5)])
    parts = lm.lora_fragments(data, 3)
    for part in parts[:-1]:
        send(app, fid, "lora", part)
    assert send(app, fid, "lora", parts[-1])["error"] == "conservation_violation"
    broken = bytearray(lm.frame(0, deltas(0)))
    broken[20] ^= 0xFF
    parts = lm.optical_fragments(bytes(broken), 4)
    assert send(app, fid, "optical", parts[0])["status"] == "corrupt"
    window = app.get(f"/api/fields/{fid}/transducers/t1/window").json()
    assert window["stats"]["corrupt"] == 1 and window["stats"]["rejected"] == 1
