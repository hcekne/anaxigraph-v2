"""Request 3: dialect versions, capability bitmasks and the handshake."""

from conftest import DEFAULT_BOUNDARY, STEP, assert_cells, assert_error, lm


def setup(app):
    fid = app.field(radius=2, k=0.0625)
    app.transducer(fid, "t1", [(0, 0), (1, 0), (0, 1)])
    return fid


def shake(app, fid, tid="t1", **body):
    return app.post(f"/api/fields/{fid}/transducers/{tid}/handshake", json=body)


DELTAS = [((0, 0), 0.75), ((1, 0), -0.5), ((0, 1), -0.25)]


def test_dialect_listing(app):
    body = app.get("/api/dialects").json()
    assert body["capabilities"] == 3
    json_dialect = [d for d in body["dialects"] if d["name"] == "json"]
    assert json_dialect and json_dialect[0]["id"] == 0
    assert json_dialect[0]["versions"][:3] == ["1.0", "1.1", "2.0"]
    assert [d["id"] for d in body["dialects"]] == sorted(d["id"] for d in body["dialects"])


def test_handshake_agrees_on_highest_common_version(app):
    fid = setup(app)
    view = app.get(f"/api/fields/{fid}/transducers/t1").json()
    assert view["dialect"] == {"name": "json", "version": "1.0"} and view["capabilities"] == 3
    response = shake(app, fid, dialect="json", versions=["1.1", "1.0", "9.9"], capabilities=3)
    assert response.status_code == 200
    assert response.json() == {"dialect": "json", "version": "1.1", "capabilities": 3}
    response = shake(app, fid, dialect="json", versions=["1.0", "2.0", "1.1"], capabilities=7)
    assert response.json() == {"dialect": "json", "version": "2.0", "capabilities": 3}
    response = shake(app, fid, dialect="json", versions=["1.0"], capabilities=1)
    assert response.json() == {"dialect": "json", "version": "1.0", "capabilities": 1}
    view = app.get(f"/api/fields/{fid}/transducers/t1").json()
    assert view["dialect"] == {"name": "json", "version": "1.0"} and view["capabilities"] == 1


def test_handshake_errors_keep_the_previous_agreement(app):
    fid = setup(app)
    assert shake(app, fid, dialect="json", versions=["1.1"], capabilities=3).status_code == 200
    assert_error(
        shake(app, fid, dialect="klingon", versions=["1.0"], capabilities=3),
        422,
        "unsupported_dialect",
    )
    for versions in ([], ["1"], ["1.0.0"], ["x.y"], ["256.0"], "1.0"):
        assert_error(
            shake(app, fid, dialect="json", versions=versions, capabilities=3),
            422,
            "invalid_request",
        )
    for caps in (-1, 65536, "3"):
        assert_error(
            shake(app, fid, dialect="json", versions=["1.0"], capabilities=caps),
            422,
            "invalid_request",
        )
    assert_error(
        shake(app, fid, dialect="json", versions=["3.0", "0.9"], capabilities=3),
        409,
        "no_common_version",
    )
    assert_error(
        shake(app, fid, dialect="json", versions=["1.0"], capabilities=2),
        409,
        "no_common_capability",
    )
    assert_error(
        shake(app, fid, tid="ghost", dialect="json", versions=["1.0"], capabilities=3),
        404,
        "not_found",
    )
    view = app.get(f"/api/fields/{fid}/transducers/t1").json()
    assert view["dialect"] == {"name": "json", "version": "1.1"} and view["capabilities"] == 3


def test_wire_frames_follow_the_agreement(app):
    fid = setup(app)
    model = lm.Field(2, k=0.0625, boundary=DEFAULT_BOUNDARY)
    shake(app, fid, dialect="json", versions=["2.0"], capabilities=3)
    r = app.wire(fid, "t1", lm.frame(0, DELTAS, version="1.0"))
    assert (r["status"], r.get("error")) == ("rejected", "dialect_mismatch")
    bad = lm.envelope(0, b'{"d": [[0, 0, 1.0], [1, 0, -1.0]], "x": 1}', version=(2, 0))
    assert app.wire(fid, "t1", bad)["error"] == "invalid_payload"
    bad = lm.envelope(0, b'{"d": [[0, 0]]}', version=(2, 0))
    assert app.wire(fid, "t1", bad)["error"] == "invalid_payload"
    assert app.wire(fid, "t1", lm.frame(0, DELTAS, version="2.0"))["status"] == "delivered"
    model.queue = [DELTAS]
    app.step(fid)
    model.step()
    assert_cells(app.cells(fid), model.v)


def test_json_minor_versions(app):
    fid = setup(app)
    noted = lm.envelope(0, lm.json_payload(DELTAS, note="hello"), version=(1, 1))
    assert app.wire(fid, "t1", noted)["error"] == "dialect_mismatch"
    noted_10 = lm.envelope(0, lm.json_payload(DELTAS, note="hello"), version=(1, 0))
    assert app.wire(fid, "t1", noted_10)["error"] == "invalid_payload"
    shake(app, fid, dialect="json", versions=["1.1"], capabilities=3)
    assert app.wire(fid, "t1", noted)["status"] == "delivered"
    # A minor version the server has never heard of. Request 14 adds a fallback chain.
    unknown_minor = lm.envelope(1, lm.json_payload(DELTAS), version=(1, 7))
    r = app.wire(fid, "t1", unknown_minor)
    if STEP >= 14:
        assert r["status"] == "delivered"
    else:
        assert (r["status"], r.get("error")) == ("rejected", "dialect_mismatch")


def test_nacks_require_selective_nack(app):
    fid = setup(app)
    shake(app, fid, dialect="json", versions=["1.0"], capabilities=1)
    r = app.wire(fid, "t1", lm.frame(2, DELTAS))
    assert (r["status"], r["nacks"]) == ("buffered", [])
    window = app.get(f"/api/fields/{fid}/transducers/t1/window").json()
    assert (window["buffered"], window["nacks"]) == ([2], [])
    shake(app, fid, dialect="json", versions=["1.0"], capabilities=3)
    window = app.get(f"/api/fields/{fid}/transducers/t1/window").json()
    assert (window["buffered"], window["nacks"]) == ([2], [0, 1])
