"""Request 1: micro-phases, bit-identical diffusion and the conservation audit."""

from conftest import DEFAULT_BOUNDARY, assert_cells, assert_error, conserved, lm, near

PHASES = ["emit", "propagate", "arbitrate", "commit"]


def seeded(radius: int) -> dict:
    """Deterministic, irregular starting values."""
    values = {}
    for i, cell in enumerate(lm.grid(radius)):
        if i % 3 == 0:
            values[cell] = ((i * 7919) % 1000) / 37.0 - 3.0
    return values


def test_micro_phases_in_order(app):
    fid = app.field(radius=2, k=0.1)
    app.transducer(fid, "t1", [(0, 0), (1, 0)])
    app.set_cells(fid, {(0, 0): 3.0})
    model = lm.Field(2, k=0.1, boundary=DEFAULT_BOUNDARY)
    model.set({(0, 0): 3.0})
    deltas = [((0, 0), -1.25), ((1, 0), 1.25)]
    assert app.frame(fid, "t1", deltas).status_code == 202
    model.queue = [deltas]
    body = app.summary(fid)
    assert (body["tick"], body["sub_tick"], body["phase"]) == (0, 0, "emit")
    for expected_sub in (1, 2, 3):
        body = app.step(fid, sub_ticks=1)
        model.run(1)
        assert (body["tick"], body["sub_tick"], body["phase"]) == (
            0,
            expected_sub,
            PHASES[expected_sub],
        )
        assert_cells(app.cells(fid), model.v)
    body = app.step(fid, sub_ticks=1)
    model.run(1)
    assert (body["tick"], body["sub_tick"], body["phase"]) == (1, 0, "emit")
    assert_cells(app.cells(fid), model.v)


def test_frames_accepted_mid_tick_wait_for_next_emit(app):
    fid = app.field(radius=2, k=0.0625)
    app.transducer(fid, "t1", [(0, 0), (0, 1)])
    start = {(0, 0): 2.0, (0, 1): 1.0}
    app.set_cells(fid, start)
    model = lm.Field(2, k=0.0625, boundary=DEFAULT_BOUNDARY)
    model.set(start)
    app.step(fid, sub_ticks=1)
    model.run(1)
    deltas = [((0, 0), 0.5), ((0, 1), -0.5)]
    assert app.frame(fid, "t1", deltas).json() == {"queued": 1}
    app.step(fid, sub_ticks=3)
    model.run(3)
    assert_cells(app.cells(fid), model.v)
    model.queue = [deltas]
    app.step(fid, sub_ticks=1)
    model.run(1)
    assert_cells(app.cells(fid), model.v)
    body = app.step(fid, ticks=1)
    model.step(1)
    assert (body["tick"], body["sub_tick"]) == (2, 1)
    assert_cells(app.cells(fid), model.v)


def test_bit_identical_long_run(app):
    fid = app.field(radius=6, k=0.15)
    start = seeded(6)
    app.set_cells(fid, start)
    model = lm.Field(6, k=0.15, boundary=DEFAULT_BOUNDARY)
    model.set(start)
    for kind, count in (
        ("sub_ticks", 6),
        ("ticks", 5),
        ("sub_ticks", 2),
        ("ticks", 20),
        ("sub_ticks", 11),
    ):
        body = app.step(fid, **{kind: count})
        model.run(count if kind == "sub_ticks" else 4 * count)
    assert (body["tick"], body["sub_tick"]) == (model.tick, model.sub_tick)
    assert_cells(app.cells(fid), model.v)
    assert body["total"] == model.total() and body["sink"] == model.sink()


def test_audit_is_conserved_at_every_sub_tick(app):
    fid = app.field(radius=3, k=1 / 6)
    app.transducer(fid, "t1", [(3, 0), (3, -1), (2, 1)])
    start = {(3, 0): 9.0, (0, 0): 1.0 / 3.0, (-2, -1): 0.7}
    app.set_cells(fid, start)
    model = lm.Field(3, k=1 / 6, boundary=DEFAULT_BOUNDARY)
    model.set(start)
    app.frame(fid, "t1", [((3, 0), -4.5), ((3, -1), 3.0), ((2, 1), 1.5)])
    model.queue = [[((3, 0), -4.5), ((3, -1), 3.0), ((2, 1), 1.5)]]
    for _ in range(4 * 6 + 2):
        audit = app.audit(fid)
        expected = model.audit()
        assert (audit["tick"], audit["sub_tick"]) == (model.tick, model.sub_tick)
        assert audit["cells_total"] == expected["cells_total"]
        assert audit["sink_total"] == expected["sink_total"]
        assert near(audit["expected_total"], expected["expected_total"], 1e-12)
        assert near(
            audit["drift"],
            audit["cells_total"] + audit["sink_total"] - audit["expected_total"],
            1e-12,
        )
        assert conserved(audit), audit
        app.step(fid, sub_ticks=1)
        model.run(1)


def test_step_validation_and_mid_tick_edits(app):
    fid = app.field(radius=1)
    for body in (
        {"ticks": 1, "sub_ticks": 1},
        {"sub_ticks": 0},
        {"sub_ticks": 4001},
        {"sub_ticks": 1.5},
    ):
        assert_error(app.post(f"/api/fields/{fid}/step", json=body), 422, "invalid_request")
    assert app.step(fid)["tick"] == 1
    app.step(fid, sub_ticks=2)
    response = app.put(f"/api/fields/{fid}/cells", json={"cells": [{"q": 0, "r": 0, "value": 1.0}]})
    assert_error(response, 409, "mid_tick")
    app.step(fid, sub_ticks=2)
    app.set_cells(fid, {(0, 0): 1.0})
    audit = app.audit(fid)
    assert audit["expected_total"] == 1.0 and conserved(audit)
    assert_error(app.get("/api/fields/missing/audit"), 404, "not_found")
