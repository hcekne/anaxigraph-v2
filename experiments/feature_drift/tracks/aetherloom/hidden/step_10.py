"""Request 10: persistence, checkpoints and restore to any (tick, sub_tick)."""

from conftest import DEFAULT_BOUNDARY, assert_cells, assert_error, lm

START = {(0, 0): 12.0, (2, -1): 3.5, (-3, 1): 1.25, (1, 2): -2.0}


def checkpoints(app, fid):
    response = app.get(f"/api/fields/{fid}/checkpoints")
    assert response.status_code == 200, response.text
    return response.json()


def restore(app, fid, tick, sub_tick=0):
    response = app.post(f"/api/fields/{fid}/restore", json={"tick": tick, "sub_tick": sub_tick})
    assert response.status_code == 200, response.text
    return response.json()


def test_schedule_and_manual_checkpoints(app):
    fid = app.field(radius=3, k=0.1)
    assert app.put(f"/api/fields/{fid}/checkpoints/schedule", json={"every": 5}).json() == {
        "every": 5
    }
    app.step(fid, ticks=12)
    created = app.post(f"/api/fields/{fid}/checkpoints")
    assert (
        created.status_code == 201 and created.json()["tick"] == 12 and created.json()["bytes"] > 0
    )
    listed = checkpoints(app, fid)
    assert listed["every"] == 5 and [c["tick"] for c in listed["checkpoints"]] == [5, 10, 12]
    assert all(isinstance(c["bytes"], int) and c["bytes"] > 0 for c in listed["checkpoints"])
    app.step(fid, sub_ticks=1)
    assert_error(app.post(f"/api/fields/{fid}/checkpoints"), 409, "mid_tick")
    for every in (-1, 1001, "x"):
        assert_error(
            app.put(f"/api/fields/{fid}/checkpoints/schedule", json={"every": every}),
            422,
            "invalid_request",
        )
    assert app.put(f"/api/fields/{fid}/checkpoints/schedule", json={"every": 0}).json() == {
        "every": 0
    }
    app.step(fid, ticks=20)
    assert [c["tick"] for c in checkpoints(app, fid)["checkpoints"]] == [5, 10, 12]


def test_restore_rewinds_and_replays_bit_identically(app):
    fid = app.field(radius=4, k=0.125)
    app.set_cells(fid, START)
    app.put(f"/api/fields/{fid}/checkpoints/schedule", json={"every": 4})
    model = lm.Field(4, k=0.125, boundary=DEFAULT_BOUNDARY)
    model.set(START)
    history = {}
    for _ in range(13):
        app.step(fid)
        model.step()
        history[model.tick] = app.cells(fid)
    assert_cells(history[13], model.v)
    body = restore(app, fid, 9)
    assert (body["tick"], body["sub_tick"]) == (9, 0)
    assert_cells(app.cells(fid), history[9])
    assert [c["tick"] for c in checkpoints(app, fid)["checkpoints"]] == [4, 8]
    app.step(fid, ticks=4)
    assert_cells(app.cells(fid), history[13])
    body = restore(app, fid, 6, 2)
    assert (body["tick"], body["sub_tick"]) == (6, 2)
    assert_cells(app.cells(fid), history[6])
    app.step(fid, sub_ticks=2)
    assert_cells(app.cells(fid), history[7])
    body = restore(app, fid, 30, 1)  # fast-forward from the checkpoint at 4
    model = lm.Field(4, k=0.125, boundary=DEFAULT_BOUNDARY)
    model.set(START)
    model.run(4 * 30 + 1)
    assert (body["tick"], body["sub_tick"]) == (30, 1)
    assert_cells(app.cells(fid), model.v)
    assert [c["tick"] for c in checkpoints(app, fid)["checkpoints"]][-1] == 28


def test_checkpoints_capture_queue_window_and_agreements(app):
    fid = app.field(radius=2, k=0.0625)
    app.transducer(fid, "t1", [(0, 0), (1, 0), (0, 1)])
    first = [((0, 0), 1.0), ((1, 0), -1.0)]
    third = [((0, 1), 0.5), ((1, 0), -0.5)]
    assert app.wire(fid, "t1", lm.frame(0, first))["status"] == "delivered"
    assert app.wire(fid, "t1", lm.frame(2, third))["status"] == "buffered"
    assert app.post(f"/api/fields/{fid}/checkpoints").status_code == 201
    shake = {"dialect": "json", "versions": ["2.0"], "capabilities": 1}
    assert app.post(f"/api/fields/{fid}/transducers/t1/handshake", json=shake).status_code == 200
    assert app.wire(fid, "t1", lm.frame(1, [((0, 0), 0.25), ((0, 1), -0.25)], version="2.0"))[
        "delivered"
    ] == [1, 2]
    app.step(fid, ticks=3)
    restore(app, fid, 0)
    window = app.get(f"/api/fields/{fid}/transducers/t1/window").json()
    assert (window["next_seq"], window["buffered"], window["nacks"]) == (1, [2], [1])
    view = app.get(f"/api/fields/{fid}/transducers/t1").json()
    assert view["dialect"] == {"name": "json", "version": "1.0"} and view["capabilities"] == 3
    model = lm.Field(2, k=0.0625, boundary=DEFAULT_BOUNDARY)
    model.queue = [first]
    app.step(fid, ticks=2)
    model.step(2)
    assert_cells(app.cells(fid), model.v)
    audit = app.audit(fid)
    assert audit["ok"] and audit["expected_total"] == 0.0


def test_restore_errors(app):
    fid = app.field(radius=1)
    app.step(fid, ticks=3)
    assert_error(app.post(f"/api/fields/{fid}/restore", json={"tick": 2}), 404, "no_checkpoint")
    app.post(f"/api/fields/{fid}/checkpoints")
    for body in ({"tick": -1}, {"tick": 3, "sub_tick": 4}, {"tick": "x"}, {}):
        assert_error(app.post(f"/api/fields/{fid}/restore", json=body), 422, "invalid_request")
    assert_error(app.post(f"/api/fields/{fid}/restore", json={"tick": 2}), 404, "no_checkpoint")
    assert app.summary(fid)["tick"] == 3
    assert_error(app.post("/api/fields/nope/restore", json={"tick": 1}), 404, "not_found")


def test_everything_survives_a_restart(fresh_app):
    app = fresh_app
    fid = app.field(
        radius=3,
        planes=[{"name": "heat", "k": 0.1}, {"name": "ion", "k": 0.05}],
        coupling=[[0, 0.125], [0.0625, 0]],
    )
    app.set_cells(fid, {(0, 0): 8.0, (3, 0): 2.0})
    app.set_cells(fid, {(1, 1): 4.0}, plane="ion")
    app.transducer(fid, "t1", [(0, 0), (1, 0)])
    app.transducer(fid, "bad", [(-1, 0)])
    app.put(f"/api/fields/{fid}/policy", json={"source": "if(v > 5, 0.5, 1)", "fuel": 7})
    app.put(f"/api/fields/{fid}/checkpoints/schedule", json={"every": 3})
    app.step(fid, ticks=4)
    assert app.wire(fid, "t1", lm.frame(1, [((0, 0), -1.0), ((1, 0), 1.0)]))["status"] == "buffered"
    app.frame(fid, "t1", [((0, 0), -2.0), ((1, 0), 2.0)])
    app.frame(fid, "bad", [((-1, 0), 1.0)])
    app.step(fid, sub_ticks=2)
    paths = [
        "",
        "/cells",
        "/audit",
        "/policy",
        "/checkpoints",
        "/transducers",
        "/transducers/t1/window",
        "/dead-letters",
        "/quarantine",
    ]
    before = {p: app.get(f"/api/fields/{fid}{p}").json() for p in paths}
    app.restart()
    after = {p: app.get(f"/api/fields/{fid}{p}").json() for p in paths}
    assert after == before
    app.step(fid, ticks=5)
    stepped = app.cells(fid)
    restore(app, fid, 6)
    app.step(fid, sub_ticks=4 * 3 + 2)
    assert app.cells(fid) == stepped
