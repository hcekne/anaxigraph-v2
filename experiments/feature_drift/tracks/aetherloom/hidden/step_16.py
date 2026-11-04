"""Request 16: telemetry consumers, acknowledgements and coalescing under backpressure."""

from conftest import DEFAULT_BOUNDARY, assert_error, lm

START = {(0, 0): 10.0, (2, -1): 4.0, (-2, 2): 1.0}
WATCH = [(0, 0), (1, 0), (2, -1)]


def consumer(app, fid, cid, **extra):
    return app.post(f"/api/fields/{fid}/consumers", json={"id": cid, **extra})


def frames(app, fid, cid):
    response = app.get(f"/api/fields/{fid}/consumers/{cid}/frames")
    assert response.status_code == 200, response.text
    return response.json()


def setup(app, watch=WATCH):
    fid = app.field(radius=3, k=0.125)
    app.set_cells(fid, START)
    model = lm.Field(3, k=0.125, boundary=DEFAULT_BOUNDARY)
    model.set(START)
    cells = [{"q": q, "r": r} for q, r in watch]
    assert consumer(app, fid, "dash", cells=cells).status_code == 201
    return fid, model


def model_frames(model, ticks, watch=WATCH, frames_by_tick=None):
    """Plain per-tick telemetry frames the model expects."""
    rows = []
    for _ in range(ticks):
        before = {c: model.v[c] for c in watch}
        if frames_by_tick and model.tick in frames_by_tick:
            model.queue = frames_by_tick[model.tick]
        model.step()
        rows.append(
            {
                "from_tick": model.tick - 1,
                "to_tick": model.tick - 1,
                "coalesced": 1,
                "deltas": [
                    {"q": q, "r": r, "delta": model.v[(q, r)] - before[(q, r)]} for q, r in watch
                ],
                "events": [],
            }
        )
    return rows


def merge(run):
    if len(run) == 1:
        return run[0]
    deltas = [dict(d) for d in run[0]["deltas"]]
    for frame in run[1:]:
        for row, extra in zip(deltas, frame["deltas"]):
            row["delta"] = row["delta"] + extra["delta"]
    return {
        "from_tick": run[0]["from_tick"],
        "to_tick": run[-1]["to_tick"],
        "coalesced": sum(f["coalesced"] for f in run),
        "deltas": deltas,
        "events": [],
    }


def test_registration_and_validation(app):
    fid, _ = setup(app)
    listed = app.get(f"/api/fields/{fid}/consumers").json()
    assert listed["consumers"] == [{"id": "dash", "plane": "scalar", "cells": 3, "lag": 0}]
    assert consumer(app, fid, "all").json() == {
        "id": "all",
        "plane": "scalar",
        "cells": 37,
        "lag": 0,
    }
    assert_error(consumer(app, fid, "dash"), 409, "conflict")
    assert_error(consumer(app, fid, "x1", plane="nope"), 422, "invalid_request")
    assert_error(consumer(app, fid, "x2", cells=[{"q": 9, "r": 0}]), 422, "out_of_grid")
    assert_error(consumer(app, fid, "x3", cells=[]), 422, "invalid_request")
    assert_error(
        consumer(app, fid, "x4", cells=[{"q": 0, "r": 0}, {"q": 0, "r": 0}]), 422, "invalid_request"
    )
    assert app.delete(f"/api/fields/{fid}/consumers/all").status_code == 204
    assert_error(app.delete(f"/api/fields/{fid}/consumers/all"), 404, "not_found")
    assert_error(app.get(f"/api/fields/{fid}/consumers/all/frames"), 404, "not_found")


def test_frames_and_acknowledgements(app):
    fid, model = setup(app)
    app.transducer(fid, "t1", [(0, 0), (1, 0)])
    moves = {1: [[((0, 0), -2.0), ((1, 0), 2.0)]]}
    app.step(fid)
    app.frame(fid, "t1", moves[1][0])
    app.step(fid, ticks=3)
    expected = model_frames(model, 4, frames_by_tick=moves)
    body = frames(app, fid, "dash")
    assert body == {"lag": 4, "frames": expected}
    assert app.post(f"/api/fields/{fid}/consumers/dash/ack", json={"through_tick": 1}).json() == {
        "lag": 2
    }
    assert frames(app, fid, "dash")["frames"] == expected[2:]
    assert_error(
        app.post(f"/api/fields/{fid}/consumers/dash/ack", json={"through_tick": "x"}),
        422,
        "invalid_request",
    )
    app.step(fid, sub_ticks=2)
    assert frames(app, fid, "dash")["lag"] == 2


def test_coalescing_after_three_windows(app):
    fid, model = setup(app)
    app.step(fid, ticks=24)
    expected = model_frames(model, 24)
    assert frames(app, fid, "dash") == {"lag": 24, "frames": expected}
    app.step(fid)
    expected += model_frames(model, 1)
    assert frames(app, fid, "dash") == {"lag": 1, "frames": [merge(expected)]}
    app.step(fid, ticks=3)
    later = model_frames(model, 3)
    assert frames(app, fid, "dash") == {"lag": 4, "frames": [merge(expected)] + later}


def test_events_are_never_merged(app):
    fid, model = setup(app)
    app.transducer(fid, "bad", [(-3, 3)])
    app.step(fid, ticks=5)
    stage = app.post(f"/api/fields/{fid}/policy/stage", json={"source": "if(v > 2, 0.5, 1)"})
    assert stage.json()["activates_at_tick"] == 5
    app.step(fid, ticks=7)
    for _ in range(3):
        app.frame(fid, "bad", [((-3, 3), 1.0)])
    app.step(fid, ticks=13)  # frame 25 (tick 24) triggers coalescing
    body = frames(app, fid, "dash")
    shape = [(f["from_tick"], f["to_tick"], f["coalesced"]) for f in body["frames"]]
    assert shape == [(0, 4, 5), (5, 5, 1), (6, 11, 6), (12, 12, 1), (13, 24, 12)]
    assert body["frames"][1]["events"] == [
        {"type": "policy_activated", "source": "if(v > 2, 0.5, 1)"}
    ]
    assert body["frames"][3]["events"] == [{"type": "quarantined", "transducer": "bad"}]
    model.step(5)
    model.gate = lambda f, c: 0.5 if f.v[c] > 2 else 1.0
    before = {c: model.v[c] for c in WATCH}
    model.step(1)
    assert body["frames"][1]["deltas"] == [
        {"q": q, "r": r, "delta": model.v[(q, r)] - before[(q, r)]} for q, r in WATCH
    ]
    app.step(fid, ticks=2)
    assert frames(app, fid, "dash")["lag"] == 7


def test_plane_selection(app):
    fid = app.field(
        radius=2,
        planes=[{"name": "a", "k": 0.1}, {"name": "b", "k": 0.05}],
        coupling=[[0, 0.5], [0, 0]],
    )
    app.set_cells(fid, {(0, 0): 4.0}, plane="a")
    consumer(app, fid, "bwatch", plane="b", cells=[{"q": 0, "r": 0}])
    model = lm.Field(
        2, planes=[("a", 0.1), ("b", 0.05)], coupling=[[0, 0.5], [0, 0]], boundary=DEFAULT_BOUNDARY
    )
    model.set({(0, 0): 4.0}, 0)
    app.step(fid)
    model.step()
    assert frames(app, fid, "bwatch")["frames"][0]["deltas"] == [
        {"q": 0, "r": 0, "delta": model.values[1][(0, 0)] - 0.0}
    ]
