"""Request 18: HMAC-signed wire frames, replay windows and watermarks that outlive restores."""

import base64

from conftest import DEFAULT_BOUNDARY, assert_cells, assert_error, lm

KEY = bytes(range(32))
OTHER = b"k" * 16


def setup(app, key=KEY):
    fid = app.field(radius=2, k=0.0625)
    app.transducer(fid, "t1", [(0, 0), (1, 0), (0, 1)])
    response = app.put(
        f"/api/fields/{fid}/transducers/t1/key", json={"key": base64.b64encode(key).decode()}
    )
    assert response.status_code == 204
    return fid


def moves(seq):
    a = 0.25 * (seq + 1)
    return [((0, 0), a), ((1, 0), -a)]


def signed(seq, counter, key=KEY):
    return lm.attest(lm.frame(seq, moves(seq)), key, counter)


def view(app, fid):
    return app.get(f"/api/fields/{fid}/transducers/t1").json()


def test_keys_are_validated_and_never_returned(app):
    fid = app.field(radius=1)
    app.transducer(fid, "t1", [(0, 0)])
    assert view(app, fid)["attested"] is False and view(app, fid)["watermark"] is None
    for key in (
        "@@@",
        base64.b64encode(b"short").decode(),
        base64.b64encode(b"x" * 65).decode(),
        5,
    ):
        assert_error(
            app.put(f"/api/fields/{fid}/transducers/t1/key", json={"key": key}),
            422,
            "invalid_request",
        )
    secret = base64.b64encode(KEY).decode()
    assert app.put(f"/api/fields/{fid}/transducers/t1/key", json={"key": secret}).status_code == 204
    assert view(app, fid)["attested"] is True
    for path in ("", "/transducers", "/transducers/t1", "/transducers/t1/window"):
        assert secret not in app.get(f"/api/fields/{fid}{path}").text


def test_signed_frames_and_signature_failures(app):
    fid = setup(app)
    model = lm.Field(2, k=0.0625, boundary=DEFAULT_BOUNDARY)
    assert app.wire(fid, "t1", lm.frame(0, moves(0)))["status"] == "corrupt"  # no trailer
    r = app.wire(fid, "t1", signed(0, 1, key=OTHER))
    assert (r["status"], r.get("error")) == ("rejected", "bad_signature")
    good = bytearray(signed(0, 1))
    good[13 + len(lm.encode("json", moves(0))) + 7] ^= 1  # flip a counter bit
    assert app.wire(fid, "t1", bytes(good))["error"] == "bad_signature"
    tampered = bytearray(signed(0, 1))
    tampered[-1] ^= 1
    assert app.wire(fid, "t1", bytes(tampered))["error"] == "bad_signature"
    assert app.wire(fid, "t1", signed(0, 1))["status"] == "delivered"
    assert view(app, fid)["watermark"] == 1
    model.queue = [moves(0)]
    app.step(fid)
    model.step()
    assert_cells(app.cells(fid), model.v)
    unsigned = app.field(radius=1)
    app.transducer(unsigned, "t1", [(0, 0), (1, 0)])
    assert app.wire(unsigned, "t1", lm.frame(0, moves(0)))["status"] == "delivered"


def test_replay_window(app):
    fid = setup(app)
    assert app.wire(fid, "t1", signed(0, 100))["status"] == "delivered"
    assert app.wire(fid, "t1", signed(0, 100))["error"] == "replayed"
    assert app.wire(fid, "t1", signed(1, 90))["status"] == "delivered"
    assert app.wire(fid, "t1", signed(2, 37))["status"] == "delivered"  # 100 - 64 < 37
    assert app.wire(fid, "t1", signed(3, 36))["error"] == "replayed"  # 36 <= 100 - 64
    assert app.wire(fid, "t1", signed(3, 90))["error"] == "replayed"
    # accepted counters are spent even when the window drops the frame
    assert app.wire(fid, "t1", signed(0, 150))["status"] == "duplicate"
    assert app.wire(fid, "t1", signed(3, 150))["error"] == "replayed"
    assert app.wire(fid, "t1", signed(3, 151))["status"] == "delivered"
    assert view(app, fid)["watermark"] == 151


def test_restores_do_not_rewind_watermarks(app):
    fid = setup(app)
    assert app.post(f"/api/fields/{fid}/checkpoints").status_code == 201
    captured = signed(0, 5)
    assert app.wire(fid, "t1", captured)["status"] == "delivered"
    app.step(fid, ticks=2)
    app.post(f"/api/fields/{fid}/restore", json={"tick": 0})
    assert app.get(f"/api/fields/{fid}/transducers/t1/window").json()["next_seq"] == 0
    assert view(app, fid)["watermark"] == 5
    assert app.wire(fid, "t1", captured)["error"] == "replayed"
    assert app.wire(fid, "t1", signed(0, 6))["status"] == "delivered"
    secret = base64.b64encode(OTHER).decode()
    assert app.put(f"/api/fields/{fid}/transducers/t1/key", json={"key": secret}).status_code == 204
    assert app.wire(fid, "t1", signed(1, 6, key=OTHER))["error"] == "replayed"
    assert app.wire(fid, "t1", signed(1, 7))["error"] == "bad_signature"
    assert app.wire(fid, "t1", signed(1, 7, key=OTHER))["status"] == "delivered"


def test_links_carry_signed_frames_and_watermarks_survive_restart(fresh_app):
    app = fresh_app
    fid = setup(app)
    for part in lm.lora_fragments(signed(0, 9), 4):
        last = app.post(f"/api/fields/{fid}/transducers/t1/links/lora", content=part).json()
    assert last["status"] == "delivered"
    app.restart()
    assert view(app, fid)["watermark"] == 9
    assert app.wire(fid, "t1", signed(1, 9))["error"] == "replayed"
    assert app.wire(fid, "t1", signed(1, 10))["status"] == "delivered"
