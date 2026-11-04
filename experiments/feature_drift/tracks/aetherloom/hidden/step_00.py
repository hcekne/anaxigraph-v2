"""Request 0: fields, hex grid, diffusion with the boundary rule, transducers and frames."""

from conftest import DEFAULT_BOUNDARY, assert_cells, assert_error, lm, near


def test_field_lifecycle_and_validation(app):
    fid = app.field(radius=3, k=0.1)
    body = app.summary(fid)
    assert {k: body[k] for k in ("id", "radius", "k", "tick", "cells", "total", "sink")} == {
        "id": fid,
        "radius": 3,
        "k": 0.1,
        "tick": 0,
        "cells": 37,
        "total": 0.0,
        "sink": 0.0,
    }
    assert fid in [f["id"] for f in app.get("/api/fields").json()["fields"]]
    response = app.post("/api/fields", json={"id": fid, "radius": 2, "k": 0.1})
    assert_error(response, 409, "conflict")
    bad = [
        {"id": "ok-1", "radius": -1, "k": 0.1},
        {"id": "ok-1", "radius": 33, "k": 0.1},
        {"id": "ok-1", "radius": 2, "k": 0},
        {"id": "ok-1", "radius": 2, "k": 0.2},
        {"id": "ok-1", "radius": "two", "k": 0.1},
        {"id": "Bad Id", "radius": 2, "k": 0.1},
        {"radius": 2, "k": 0.1},
    ]
    for payload in bad:
        assert_error(app.post("/api/fields", json=payload), 422, "invalid_request")
    assert_error(app.get("/api/fields/missing"), 404, "not_found")
    assert_error(app.post("/api/fields/missing/step", json={"ticks": 1}), 404, "not_found")
    assert app.summary(app.field(radius=0, k=1 / 6))["cells"] == 1


def test_cells_canonical_order_and_updates(app):
    fid = app.field(radius=2)
    listed = app.get(f"/api/fields/{fid}/cells").json()
    assert listed["tick"] == 0
    assert [(c["q"], c["r"]) for c in listed["cells"]] == lm.grid(2)
    assert all(c["value"] == 0.0 for c in listed["cells"])
    app.set_cells(fid, {(0, 0): 12.5, (-2, 1): 0.25, (1, 1): -3.0})
    body = app.summary(fid)
    assert body["total"] == 9.75 and body["cells"] == 19
    cells = app.cells(fid)
    assert cells[(0, 0)] == 12.5 and cells[(-2, 1)] == 0.25 and cells[(1, 1)] == -3.0
    response = app.put(
        f"/api/fields/{fid}/cells",
        json={"cells": [{"q": 0, "r": 0, "value": 1.0}, {"q": 2, "r": 1, "value": 1.0}]},
    )
    assert_error(response, 422, "out_of_grid")
    assert app.cells(fid)[(0, 0)] == 12.5
    response = app.put(f"/api/fields/{fid}/cells", json={"cells": [{"q": 0, "r": 0, "value": "x"}]})
    assert_error(response, 422, "invalid_request")


def test_diffusion_follows_the_rule(app):
    fid = app.field(radius=3, k=0.125)
    start = {(0, 0): 40.0, (3, -3): 8.0, (-1, 2): 5.5, (2, 0): -1.5}
    app.set_cells(fid, start)
    model = lm.Field(3, k=0.125, boundary=DEFAULT_BOUNDARY)
    model.set(start)
    for ticks in (1, 4, 7):
        body = app.step(fid, ticks=ticks)
        model.step(ticks)
        assert body["tick"] == model.tick
        assert near(body["total"], model.total()) and near(body["sink"], model.sink())
        assert_cells(app.cells(fid), model.v, tol=1e-9)
    assert app.step(fid)["tick"] == 13
    for ticks in (0, 1001, "x"):
        response = app.post(f"/api/fields/{fid}/step", json={"ticks": ticks})
        assert_error(response, 422, "invalid_request")


def test_boundary_flux_reaches_the_sink(app):
    fid = app.field(radius=1, k=0.1)
    app.set_cells(fid, {(1, 0): 10.0})
    model = lm.Field(1, k=0.1, boundary=DEFAULT_BOUNDARY)
    model.set({(1, 0): 10.0})
    body = app.step(fid, ticks=3)
    model.step(3)
    assert near(body["sink"], model.sink()) and near(body["total"], model.total())
    if DEFAULT_BOUNDARY == "absorbing":
        assert body["sink"] > 0 and near(body["total"] + body["sink"], 10.0)
    else:
        assert body["sink"] == 0.0 and near(body["total"], 10.0)


def test_transducers_registration(app):
    fid = app.field(radius=2)
    created = app.transducer(fid, "t1", [(1, 0), (0, 0), (-1, 1)])
    assert created["id"] == "t1"
    assert created["cells"] == [{"q": -1, "r": 1}, {"q": 0, "r": 0}, {"q": 1, "r": 0}]
    app.transducer(fid, "a0", [(2, -2)])
    listed = app.get(f"/api/fields/{fid}/transducers").json()["transducers"]
    assert [t["id"] for t in listed] == ["a0", "t1"]
    assert app.get(f"/api/fields/{fid}/transducers/t1").json()["cells"][0] == {"q": -1, "r": 1}
    post = lambda body: app.post(f"/api/fields/{fid}/transducers", json=body)  # noqa: E731
    assert_error(post({"id": "t1", "cells": [{"q": 0, "r": 1}]}), 409, "conflict")
    assert_error(post({"id": "t2", "cells": [{"q": 0, "r": 0}]}), 409, "cell_owned")
    assert_error(post({"id": "t2", "cells": [{"q": 5, "r": 0}]}), 422, "out_of_grid")
    assert_error(post({"id": "t2", "cells": []}), 422, "invalid_request")
    assert_error(
        post({"id": "t2", "cells": [{"q": 0, "r": 1}, {"q": 0, "r": 1}]}), 422, "invalid_request"
    )
    assert_error(app.get(f"/api/fields/{fid}/transducers/nope"), 404, "not_found")


def test_frames_are_validated_queued_and_applied(app):
    fid = app.field(radius=2, k=0.0625)
    app.transducer(fid, "t1", [(0, 0), (1, 0), (0, 1)])
    app.transducer(fid, "t2", [(-1, 0)])
    app.set_cells(fid, {(0, 0): 4.0, (-1, 0): 2.0})
    model = lm.Field(2, k=0.0625, boundary=DEFAULT_BOUNDARY)
    model.set({(0, 0): 4.0, (-1, 0): 2.0})
    assert_error(app.frame(fid, "t1", [((-1, 0), 1.0), ((0, 0), -1.0)]), 422, "foreign_cell")
    assert_error(
        app.frame(fid, "t1", [((0, 0), 1.0), ((1, 0), -0.5)]), 422, "conservation_violation"
    )
    assert_error(app.frame(fid, "t1", []), 422, "invalid_request")
    assert_error(app.frame(fid, "nope", [((0, 0), 0.0)]), 404, "not_found")
    first = [((0, 0), -1.5), ((1, 0), 1.0), ((0, 1), 0.5)]
    second = [((1, 0), -0.25), ((0, 1), 0.25)]
    response = app.frame(fid, "t1", first)
    assert response.status_code == 202 and response.json() == {"queued": 1}
    assert app.frame(fid, "t1", second).json() == {"queued": 2}
    assert app.cells(fid)[(0, 0)] == 4.0
    model.queue = [first, second]
    app.step(fid)
    model.step()
    assert_cells(app.cells(fid), model.v, tol=1e-9)
    assert app.frame(fid, "t2", [((-1, 0), 0.0)]).json() == {"queued": 1}
