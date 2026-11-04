"""Request 8: multi-plane fields with cross-plane coupling."""

from conftest import DEFAULT_BOUNDARY, assert_cells, assert_error, conserved, lm

PLANES = [{"name": "thermal", "k": 0.1}, {"name": "pressure", "k": 0.05}, {"name": "em", "k": 0.15}]
COUPLING = [[0.0, 0.02, 0.0], [0.01, 0.0, 0.03], [0.125, 0.0, 0.0]]


def make(app, boundary=None, radius=3):
    extra = {"planes": PLANES, "coupling": COUPLING}
    if boundary:
        extra["boundary"] = boundary
    fid = app.field(radius=radius, **extra)
    model = lm.Field(
        radius,
        planes=[(p["name"], p["k"]) for p in PLANES],
        coupling=COUPLING,
        boundary=boundary or DEFAULT_BOUNDARY,
    )
    return fid, model


def load(app, fid, model):
    data = [
        ({(0, 0): 10.0, (3, -3): 2.0, (1, 1): 0.5}, "thermal", 0),
        ({(0, 0): 1.0, (-2, 1): 4.0}, "pressure", 1),
        ({(-3, 3): 6.0, (2, 0): -1.25}, "em", 2),
    ]
    for values, name, index in data:
        app.set_cells(fid, values, plane=name)
        model.set(values, plane=index)


def check(app, fid, model):
    rows = app.plane_cells(fid)
    for index in range(3):
        assert_cells({c: v[index] for c, v in rows.items()}, model.values[index])
    assert app.cells(fid) == {c: v[0] for c, v in rows.items()}


def test_creation_and_validation(app):
    fid, _ = make(app)
    body = app.summary(fid)
    assert body["planes"] == PLANES and body["coupling"] == COUPLING and body["k"] == 0.1
    assert body["sinks"] == {"thermal": 0.0, "pressure": 0.0, "em": 0.0}
    single = app.summary(app.field(radius=1, k=0.1))
    assert single["planes"] == [{"name": "scalar", "k": 0.1}] and single["coupling"] == [[0.0]]
    assert app.plane_cells(app.field(radius=1, k=0.1))[(0, 0)] == [0.0]
    two = [{"name": "a", "k": 0.1}, {"name": "b", "k": 0.1}]
    bad = [
        {"planes": PLANES, "k": 0.1},
        {"planes": []},
        {"planes": [{"name": f"p{i}", "k": 0.1} for i in range(9)]},
        {"planes": [{"name": "a", "k": 0.1}, {"name": "a", "k": 0.1}]},
        {"planes": [{"name": "Bad", "k": 0.1}]},
        {"planes": [{"name": "a", "k": 0.5}]},
        {"planes": two, "coupling": [[0, 0.1]]},
        {"planes": two, "coupling": [[0.1, 0], [0, 0]]},
        {"planes": two, "coupling": [[0, -0.1], [0, 0]]},
        {"planes": two, "coupling": [[0, 1.5], [0, 0]]},
        {"radius": 2},
    ]
    for extra in bad:
        payload = {"id": "bad-planes", "radius": 2, **extra}
        assert_error(app.post("/api/fields", json=payload), 422, "invalid_request")
    response = app.put(
        f"/api/fields/{fid}/cells",
        json={"cells": [{"q": 0, "r": 0, "value": 1.0, "plane": "nope"}]},
    )
    assert_error(response, 422, "invalid_request")


def test_coupled_planes_are_bit_identical(app):
    for boundary in ("absorbing", "reflective"):
        fid, model = make(app, boundary)
        load(app, fid, model)
        body = app.step(fid, ticks=7)
        model.step(7)
        check(app, fid, model)
        assert body["total"] == model.total() and body["sink"] == model.sink()
        assert body["sinks"] == {p["name"]: model.sinks[i] for i, p in enumerate(PLANES)}


def test_frames_move_the_first_plane_and_gates_apply_to_all(app):
    fid, model = make(app)
    load(app, fid, model)
    app.transducer(fid, "t1", [(0, 0), (1, 0)])
    deltas = [((0, 0), -3.0), ((1, 0), 3.0)]
    app.frame(fid, "t1", deltas)
    model.queue = [deltas]
    assert (
        app.put(f"/api/fields/{fid}/policy", json={"source": "if(v > 2, 0.5, 1)"}).status_code
        == 200
    )
    model.gate = lambda f, c: 0.5 if f.v[c] > 2 else 1.0
    app.step(fid, ticks=5)
    model.step(5)
    check(app, fid, model)


def test_audit_covers_every_plane(app):
    fid, model = make(app, "absorbing")
    load(app, fid, model)
    for _ in range(4 * 5 + 1):
        audit = app.audit(fid)
        assert conserved(audit), audit
        assert audit["cells_total"] == model.total() and audit["sink_total"] == model.sink()
        for index, plane in enumerate(PLANES):
            total = 0.0
            for cell in model.cells:
                total = total + model.values[index][cell]
            assert audit["planes"][plane["name"]] == {
                "cells_total": total,
                "sink_total": model.sinks[index],
            }
        app.step(fid, sub_ticks=1)
        model.run(1)
