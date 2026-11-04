"""Request 14 (probe P2): runtime dialect aliases and minor-version fallback chains."""

import base64

from conftest import DEFAULT_BOUNDARY, assert_cells, assert_error, lm

SAMPLE = [((0, 1), 0.5), ((1, 0), -0.5)]


def alias(app, name, target):
    return app.post("/api/dialects/aliases", json={"alias": name, "target": target})


def shake(app, fid, tid, dialect, versions):
    body = {"dialect": dialect, "versions": versions, "capabilities": 3}
    return app.post(f"/api/fields/{fid}/transducers/{tid}/handshake", json=body)


def encode(app, name, version=None):
    body = {"deltas": [{"q": q, "r": r, "delta": d} for (q, r), d in SAMPLE]}
    if version:
        body["version"] = version
    return app.post(f"/api/dialects/{name}/encode", json=body)


def test_alias_lifecycle(app):
    created = alias(app, "cbor", "cborlite")
    assert created.status_code == 201
    assert created.json() == {"alias": "cbor", "target": "cborlite", "resolves_to": "cborlite"}
    assert alias(app, "vendor-cbor", "cbor").json()["resolves_to"] == "cborlite"
    assert_error(alias(app, "json", "compactbin"), 409, "conflict")
    assert_error(alias(app, "cbor", "compactbin"), 409, "conflict")
    assert_error(alias(app, "nothing", "klingon"), 422, "unsupported_dialect")
    for bad in ("Bad", "1abc", "x" * 33, ""):
        assert_error(alias(app, bad, "json"), 422, "invalid_request")
    listed = app.get("/api/dialects/aliases").json()["aliases"]
    assert {"alias": "vendor-cbor", "target": "cbor", "resolves_to": "cborlite"} in listed
    assert [a["alias"] for a in listed] == sorted(a["alias"] for a in listed)
    mapping = app.get("/api/dialects").json()["aliases"]
    assert mapping["cbor"] == "cborlite" and mapping["vendor-cbor"] == "cborlite"
    assert_error(app.delete("/api/dialects/aliases/cbor"), 409, "alias_in_use")
    assert app.delete("/api/dialects/aliases/vendor-cbor").status_code == 204
    assert app.delete("/api/dialects/aliases/cbor").status_code == 204
    assert_error(app.delete("/api/dialects/aliases/cbor"), 404, "not_found")


def test_repointing_and_cycles(app):
    alias(app, "ra", "json")
    alias(app, "rb", "ra")
    alias(app, "rc", "rb")
    assert_error(app.put("/api/dialects/aliases/ra", json={"target": "rc"}), 422, "alias_cycle")
    assert_error(app.put("/api/dialects/aliases/ra", json={"target": "ra"}), 422, "alias_cycle")
    assert app.get("/api/dialects").json()["aliases"]["rc"] == "json"
    response = app.put("/api/dialects/aliases/ra", json={"target": "deltaframe"})
    assert response.status_code == 200 and response.json()["resolves_to"] == "deltaframe"
    assert app.get("/api/dialects").json()["aliases"]["rc"] == "deltaframe"
    assert_error(app.put("/api/dialects/aliases/zz", json={"target": "json"}), 404, "not_found")
    assert_error(
        app.put("/api/dialects/aliases/ra", json={"target": "klingon"}), 422, "unsupported_dialect"
    )


def test_aliases_resolve_in_handshake_and_codecs(app):
    alias(app, "lp", "lineproto")
    alias(app, "lp2", "lp")
    fid = app.field(radius=2, k=0.0625)
    app.transducer(fid, "t1", [(0, 1), (1, 0)])
    response = shake(app, fid, "t1", "lp2", ["1.0"])
    assert response.json() == {"dialect": "lineproto", "version": "1.0", "capabilities": 3}
    assert app.get(f"/api/fields/{fid}/transducers/t1").json()["dialect"] == {
        "name": "lineproto",
        "version": "1.0",
    }
    payload = encode(app, "lp2").json()["payload"]
    assert base64.b64decode(payload) == lm.encode("lineproto", SAMPLE)
    decoded = app.post("/api/dialects/lp2/decode", json={"payload": payload}).json()
    assert decoded == {"deltas": [{"q": 0, "r": 1, "delta": 0.5}, {"q": 1, "r": 0, "delta": -0.5}]}
    assert app.wire(fid, "t1", lm.frame(0, SAMPLE, dialect="lineproto"))["status"] == "delivered"
    model = lm.Field(2, k=0.0625, boundary=DEFAULT_BOUNDARY)
    model.queue = [SAMPLE]
    app.step(fid, ticks=2)
    model.step(2)
    assert_cells(app.cells(fid), model.v)


def test_minor_version_fallback_on_the_wire(app):
    fid = app.field(radius=2, k=0.0625)
    app.transducer(fid, "j", [(0, 1), (1, 0)])
    app.transducer(fid, "c", [(-1, 0), (-1, 1)])
    shake(app, fid, "j", "json", ["1.1"])
    shake(app, fid, "c", "compactbin", ["1.0"])
    noted = lm.json_payload(SAMPLE, note="from the future")
    assert app.wire(fid, "j", lm.envelope(0, noted, version=(1, 7)))["status"] == "delivered"
    assert app.wire(fid, "j", lm.envelope(1, noted, version=(1, 0)))["error"] == "dialect_mismatch"
    assert (
        app.wire(fid, "j", lm.envelope(1, lm.encode("json", SAMPLE, "2.0"), version=(2, 5)))[
            "error"
        ]
        == "dialect_mismatch"
    )
    assert (
        app.wire(fid, "j", lm.envelope(1, noted, version=(3, 1)))["error"] == "unsupported_version"
    )
    assert app.wire(fid, "j", lm.envelope(1, noted, version=(1, 255)))["status"] == "delivered"
    c_deltas = [((-1, 0), 0.25), ((-1, 1), -0.25)]
    frame = lm.envelope(0, lm.encode("compactbin", c_deltas), dialect=1, version=(1, 9))
    assert app.wire(fid, "c", frame)["status"] == "delivered"
    frame = lm.envelope(1, lm.encode("compactbin", c_deltas), dialect=1, version=(2, 0))
    assert app.wire(fid, "c", frame)["error"] == "unsupported_version"
    model = lm.Field(2, k=0.0625, boundary=DEFAULT_BOUNDARY)
    model.queue = [SAMPLE, SAMPLE, c_deltas]
    app.step(fid)
    model.step()
    assert_cells(app.cells(fid), model.v)


def test_codec_endpoint_fallback(app):
    noted = base64.b64encode(lm.json_payload(SAMPLE, note="n")).decode()
    response = app.post("/api/dialects/json/decode", json={"version": "1.3", "payload": noted})
    assert response.status_code == 200 and len(response.json()["deltas"]) == 2
    assert_error(
        app.post("/api/dialects/json/decode", json={"version": "1.0", "payload": noted}),
        422,
        "invalid_payload",
    )
    v2 = encode(app, "json", "2.9")
    assert v2.status_code == 200
    assert (
        app.post(
            "/api/dialects/json/decode", json={"version": "2.0", "payload": v2.json()["payload"]}
        ).status_code
        == 200
    )
    assert_error(encode(app, "json", "4.0"), 422, "unsupported_version")
    assert_error(encode(app, "deltaframe", "0.9"), 422, "unsupported_version")


def test_aliases_survive_restart(fresh_app):
    alias(fresh_app, "stay", "compactbin")
    fresh_app.restart()
    assert fresh_app.get("/api/dialects").json()["aliases"] == {"stay": "compactbin"}
    assert encode(fresh_app, "stay").status_code == 200
