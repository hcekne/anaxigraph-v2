"""Request 7 (probe P1): reflective boundaries by default, absorbing on request."""

from conftest import assert_cells, assert_error, conserved, lm

RIM = {
    (3, 0): 9.0,
    (3, -3): 4.0,
    (-3, 3): 2.5,
    (0, -3): 1.25,
    (-1, 3): 0.75,
    (0, 0): 5.0,
    (2, -1): -1.5,
}


def test_default_is_reflective_and_absorbing_stays_available(app):
    reflective = app.field(radius=3, k=0.125)
    absorbing = app.field(radius=3, k=0.125, boundary="absorbing")
    assert app.summary(reflective)["boundary"] == "reflective"
    assert app.summary(absorbing)["boundary"] == "absorbing"
    assert app.summary(app.field(radius=2, boundary="reflective"))["boundary"] == "reflective"
    for boundary in ("open", "", 1):
        response = app.post(
            "/api/fields", json={"id": "bad-boundary", "radius": 2, "k": 0.1, "boundary": boundary}
        )
        assert_error(response, 422, "invalid_request")
    for fid, mode in ((reflective, "reflective"), (absorbing, "absorbing")):
        app.set_cells(fid, RIM)
        model = lm.Field(3, k=0.125, boundary=mode)
        model.set(RIM)
        body = app.step(fid, ticks=9)
        model.step(9)
        assert_cells(app.cells(fid), model.v)
        assert body["sink"] == model.sink() and body["total"] == model.total()
    assert app.summary(reflective)["sink"] == 0.0 and app.summary(absorbing)["sink"] > 0.0


def test_reflection_is_exact_at_corners_and_edges(app):
    for radius, k in ((1, 1 / 6), (2, 0.1), (4, 0.15)):
        fid = app.field(radius=radius, k=k)
        start = {
            cell: float(i % 5) - 1.0 + i / 64
            for i, cell in enumerate(lm.grid(radius))
            if lm.ring(*cell) == radius
        }
        app.set_cells(fid, start)
        model = lm.Field(radius, k=k, boundary="reflective")
        model.set(start)
        app.step(fid, sub_ticks=1)
        model.run(1)
        for _ in range(7):
            body = app.step(fid, sub_ticks=3)
            model.run(3)
            assert_cells(app.cells(fid), model.v)
        assert body["sink"] == 0.0


def test_single_cell_field_holds_its_value(app):
    fid = app.field(radius=0, k=1 / 6)
    app.set_cells(fid, {(0, 0): 7.0})
    body = app.step(fid, ticks=5)
    assert body["total"] == 7.0 and body["sink"] == 0.0


def test_conservation_at_every_sub_tick_with_frames(app):
    fid = app.field(radius=3, k=1 / 6)
    app.transducer(fid, "rim", [(3, 0), (3, -1), (2, 1), (-3, 0)])
    app.set_cells(fid, RIM)
    model = lm.Field(3, k=1 / 6, boundary="reflective")
    model.set(RIM)
    for tick in range(6):
        deltas = [((3, 0), -0.5 * tick), ((-3, 0), 0.25 * tick), ((2, 1), 0.25 * tick)]
        assert app.frame(fid, "rim", deltas).status_code == 202
        model.queue = [deltas]
        for _ in range(4):
            app.step(fid, sub_ticks=1)
            model.run(1)
            audit = app.audit(fid)
            assert conserved(audit) and audit["sink_total"] == 0.0, audit
            assert audit["cells_total"] == model.total()
    assert_cells(app.cells(fid), model.v)


def test_reflection_with_policy_gates(app):
    fid = app.field(radius=3, k=0.125)
    app.set_cells(fid, RIM)
    source = "if(ring == 3, 0.5, 1) * if(v > 3, 0.25, 1)"
    assert app.put(f"/api/fields/{fid}/policy", json={"source": source}).status_code == 200
    model = lm.Field(3, k=0.125, boundary="reflective")
    model.set(RIM)
    model.gate = lambda f, c: (0.5 if lm.ring(*c) == 3 else 1.0) * (0.25 if f.v[c] > 3 else 1.0)
    app.step(fid, ticks=8)
    model.step(8)
    assert_cells(app.cells(fid), model.v)
    audit = app.audit(fid)
    assert conserved(audit) and audit["sink_total"] == 0.0
