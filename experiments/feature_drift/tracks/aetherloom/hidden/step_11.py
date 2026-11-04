"""Request 11: quantized (bridged) transducers with compensation accumulators."""

from conftest import DEFAULT_BOUNDARY, assert_cells, assert_error, conserved, lm, near


def bridge(app, fid, tid, **body):
    return app.put(f"/api/fields/{fid}/transducers/{tid}/bridge", json=body)


def view(app, fid, tid):
    return app.get(f"/api/fields/{fid}/transducers/{tid}").json()


def residual_rows(model, tid):
    rows = model.residuals.get(tid, {})
    return [
        {"q": q, "r": r, "residual": rows[(q, r)]} for q, r in sorted(rows) if rows[(q, r)] != 0.0
    ]


def test_bridge_configuration(app):
    fid = app.field(radius=2)
    app.transducer(fid, "edge", [(0, 0), (1, 0)])
    assert view(app, fid, "edge")["bridge"] is None and view(app, fid, "edge")["residuals"] == []
    response = bridge(app, fid, "edge", bits=16, step=0.0009765625)
    assert response.status_code == 200 and response.json()["bridge"] == {
        "bits": 16,
        "step": 0.0009765625,
    }
    for body in (
        {"bits": 12, "step": 0.1},
        {"bits": 16, "step": 0},
        {"bits": 16, "step": -1.0},
        {"bits": "16", "step": 0.1},
        {"bits": 8},
    ):
        assert_error(bridge(app, fid, "edge", **body), 422, "invalid_request")
    assert_error(bridge(app, fid, "ghost", bits=8, step=0.1), 404, "not_found")
    assert bridge(app, fid, "edge", bits=8, step=0.5).json()["bridge"] == {"bits": 8, "step": 0.5}


def test_error_feedback_carries_residuals(app):
    fid = app.field(radius=2, k=0.0625)
    app.transducer(fid, "edge", [(0, 0), (1, 0), (0, 1)])
    app.transducer(fid, "float", [(-1, 0), (-1, 1)])
    bridge(app, fid, "edge", bits=16, step=1.0)
    model = lm.Field(2, k=0.0625, boundary=DEFAULT_BOUNDARY)
    model.bridges["edge"] = (16, 1.0)
    frames = [
        ("edge", [((0, 0), 0.4), ((1, 0), -0.4)]),
        ("float", [((-1, 0), 0.4), ((-1, 1), -0.4)]),
        ("edge", [((0, 0), 0.4), ((1, 0), -0.4)]),
        ("edge", [((0, 1), 2.5), ((1, 0), -2.5)]),
    ]
    for tid, deltas in frames:
        assert app.frame(fid, tid, deltas).status_code == 202
    model.queue = list(frames)
    app.step(fid, sub_ticks=1)
    model.run(1)
    assert_cells(app.cells(fid), model.v)
    assert view(app, fid, "edge")["residuals"] == residual_rows(model, "edge")
    assert view(app, fid, "float")["residuals"] == []
    audit = app.audit(fid)
    assert near(audit["residual_total"], model.residual_total(), 1e-12) and conserved(audit), audit
    app.step(fid, ticks=3)
    model.step(3)
    assert_cells(app.cells(fid), model.v)


def test_clamping_to_the_register(app):
    fid = app.field(radius=1, k=0.1)
    app.transducer(fid, "edge", [(0, 0), (1, 0)])
    bridge(app, fid, "edge", bits=8, step=0.01)
    model = lm.Field(1, k=0.1, boundary=DEFAULT_BOUNDARY)
    model.bridges["edge"] = (8, 0.01)
    queue = []
    for amount in (2.0, -0.75, 1.3):
        deltas = [((0, 0), amount), ((1, 0), -amount)]
        app.frame(fid, "edge", deltas)
        queue.append(("edge", deltas))
    model.queue = queue
    app.step(fid, sub_ticks=1)
    model.run(1)
    assert_cells(app.cells(fid), model.v)
    assert view(app, fid, "edge")["residuals"] == residual_rows(model, "edge")
    assert conserved(app.audit(fid))


def test_conservation_every_sub_tick_with_quantized_traffic(app):
    fid = app.field(radius=3, k=1 / 6)
    app.transducer(fid, "edge", [(3, 0), (2, 0), (2, 1), (0, 0)])
    bridge(app, fid, "edge", bits=16, step=2**-7)
    app.set_cells(fid, {(0, 0): 5.0, (2, 0): 1.0})
    model = lm.Field(3, k=1 / 6, boundary=DEFAULT_BOUNDARY)
    model.set({(0, 0): 5.0, (2, 0): 1.0})
    model.bridges["edge"] = (16, 2**-7)
    for tick in range(5):
        a = 0.1 * (tick + 1)
        deltas = [((0, 0), -a), ((3, 0), a / 3), ((2, 1), a - a / 3)]
        app.frame(fid, "edge", deltas)
        model.queue = [("edge", deltas)]
        for _ in range(4):
            app.step(fid, sub_ticks=1)
            model.run(1)
            audit = app.audit(fid)
            assert conserved(audit), audit
            assert near(audit["residual_total"], model.residual_total(), 1e-12)
    assert_cells(app.cells(fid), model.v)


def test_residuals_survive_restore_and_follow_the_transducer(app):
    fid = app.field(radius=2, k=0.0625)
    app.transducer(fid, "edge", [(0, 0), (1, 0)])
    bridge(app, fid, "edge", bits=16, step=0.25)
    app.frame(fid, "edge", [((0, 0), 0.1), ((1, 0), -0.1)])
    app.step(fid)
    saved = view(app, fid, "edge")["residuals"]
    assert saved == [{"q": 0, "r": 0, "residual": 0.1}, {"q": 1, "r": 0, "residual": -0.1}]
    app.post(f"/api/fields/{fid}/checkpoints")
    app.frame(fid, "edge", [((0, 0), 0.1), ((1, 0), -0.1)])
    app.step(fid)
    assert view(app, fid, "edge")["residuals"] != saved
    app.post(f"/api/fields/{fid}/restore", json={"tick": 1})
    assert view(app, fid, "edge")["residuals"] == saved
    assert conserved(app.audit(fid))
