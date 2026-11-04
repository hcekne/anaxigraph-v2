"""Request 20 (probe P3): scheduled faults, residual flush on quarantine, boundary-only effects."""

from conftest import DEFAULT_BOUNDARY, assert_cells, assert_error, conserved, lm, near

BYZ = [(-1, 0), (0, -1), (0, 0)]  # one cell in each of three sectors (sector_size 4)
START = {(0, 0): 4.0, (-1, 0): 2.0, (0, -1): 1.0, (2, -1): 3.0}
RULE = "if(v > 2, 0.5, 1)"


def deltas(tick):
    """Rounds unevenly on the 1/32 register, so the residuals do not cancel out."""
    a = 0.05 + tick / 256
    return [((0, 0), a), ((-1, 0), -a / 2), ((0, -1), -(a - a / 2))]


def build(app, consumer=True):
    fid = app.field(radius=4, k=0.125, sector_size=4)
    app.set_cells(fid, START)
    app.transducer(fid, "byz", BYZ)
    app.transducer(fid, "n1", [(1, 0)])
    app.transducer(fid, "n2", [(-1, 1)])
    app.transducer(fid, "n3", [(1, -1)])
    assert (
        app.put(
            f"/api/fields/{fid}/transducers/byz/bridge", json={"bits": 8, "step": 0.03125}
        ).status_code
        == 200
    )
    if consumer:
        cells = [{"q": q, "r": r} for q, r in BYZ]
        assert (
            app.post(
                f"/api/fields/{fid}/consumers", json={"id": "dash", "cells": cells}
            ).status_code
            == 201
        )
    model = lm.Field(4, k=0.125, boundary=DEFAULT_BOUNDARY)
    model.set(START)
    model.bridges["byz"] = (8, 0.03125)
    return fid, model


def warm_up(app, fid, model, ticks=4):
    for tick in range(ticks):
        assert app.frame(fid, "byz", deltas(tick)).status_code == 202
        app.step(fid)
        model.queue = [("byz", deltas(tick))]
        model.step()


def fault(app, fid, tick, sub_tick, action):
    return app.post(
        f"/api/fields/{fid}/faults",
        json={"at": {"tick": tick, "sub_tick": sub_tick}, "action": action},
    )


def schedule_incident(app, fid):
    assert (
        fault(app, fid, 5, 1, {"type": "stage_policy", "source": RULE, "fuel": 16}).status_code
        == 201
    )
    assert fault(app, fid, 5, 2, {"type": "quarantine", "transducer": "byz"}).json()["id"] == 2
    late = [{"q": 0, "r": 0, "delta": 0.5}, {"q": -1, "r": 0, "delta": -0.5}]
    assert (
        fault(app, fid, 5, 3, {"type": "frame", "transducer": "byz", "deltas": late}).status_code
        == 201
    )


def status(app, fid, tid="byz"):
    return app.get(f"/api/fields/{fid}/transducers/{tid}").json()["status"]


def test_incident_rehearsal(app):
    fid, model = build(app)
    warm_up(app, fid, model)
    assert app.audit(fid)["residual_total"] != 0.0
    schedule_incident(app, fid)

    def boundary(m):
        if m.tick == 6:
            m.gate = lambda f, c: 0.5 if f.v[c] > 2 else 1.0
            lm.flush_residuals(m, "byz")

    model.on_boundary = boundary
    before_tick6 = None
    for _ in range(12):
        position = (model.tick, model.sub_tick)
        if position == (6, 0):
            before_tick6 = {c: model.v[c] for c in BYZ}
        app.step(fid, sub_ticks=1)
        model.run(1)
        audit = app.audit(fid)
        assert conserved(audit), (position, audit)
        assert near(audit["residual_total"], model.residual_total(), 1e-12)
        position = (model.tick, model.sub_tick)
        if position == (5, 2):
            assert app.get(f"/api/fields/{fid}/policy/staged").status_code == 200
            assert status(app, fid) == "active"
        if position in ((5, 3), (6, 0)):
            assert status(app, fid) == "pending"
            assert app.get(f"/api/fields/{fid}/transducers/byz").json()["residuals"]
            assert_error(app.get(f"/api/fields/{fid}/policy"), 404, "no_policy")
            assert_cells(app.cells(fid), model.v)
        if position == (6, 1):
            assert status(app, fid) == "quarantined"
            assert app.get(f"/api/fields/{fid}/transducers/byz").json()["residuals"] == []
            assert app.get(f"/api/fields/{fid}/policy").json()["active_since"] == 6
            assert_cells(app.cells(fid), model.v)
    assert (model.tick, model.sub_tick) == (7, 0)
    assert_cells(app.cells(fid), model.v)
    assert [f["status"] for f in app.get(f"/api/fields/{fid}/faults").json()["faults"]] == [
        "fired"
    ] * 3
    owners = {
        t: [(c["q"], c["r"]) for c in app.get(f"/api/fields/{fid}/transducers/{t}").json()["cells"]]
        for t in ("n1", "n2", "n3")
    }
    assert owners == {"n1": [(0, 0), (1, 0)], "n2": [(-1, 0), (-1, 1)], "n3": [(0, -1), (1, -1)]}
    letters = app.get(f"/api/fields/{fid}/dead-letters").json()["frames"]
    assert [(f["reason"], f["tick"]) for f in letters] == [("quarantined", 6)]
    frames = app.get(f"/api/fields/{fid}/consumers/dash/frames").json()["frames"]
    tick6 = [f for f in frames if f["from_tick"] == 6][0]
    assert tick6["events"] == [
        {"type": "policy_activated", "source": RULE},
        {"type": "quarantined", "transducer": "byz"},
    ]
    sectors = app.get(f"/api/fields/{fid}/sectors").json()["sectors"]
    assert abs(sum(s["exchange"] for s in sectors)) < 1e-12
    expected = [{"q": q, "r": r, "delta": model.v[(q, r)] - before_tick6[(q, r)]} for q, r in BYZ]
    assert tick6["deltas"] == expected  # includes the flushed residuals


def test_how_you_step_never_changes_the_outcome(app):
    fields = []
    for _ in range(2):
        fid, model = build(app, consumer=False)
        warm_up(app, fid, model)
        schedule_incident(app, fid)
        fields.append(fid)
    a, b = fields
    order = [[s["sq"], s["sr"]] for s in app.get(f"/api/fields/{a}/sectors").json()["sectors"]]
    app.step(a, ticks=3, workers=3, sector_order=order[::-1])
    for _ in range(12):
        app.step(b, sub_ticks=1)
    assert app.cells(a) == app.cells(b)
    assert app.audit(a) == app.audit(b)
    assert (
        app.get(f"/api/fields/{a}/transducers/byz").json()
        == app.get(f"/api/fields/{b}/transducers/byz").json()
    )


def test_operator_quarantine_flushes_residuals_at_the_boundary(app):
    fid, model = build(app, consumer=False)
    warm_up(app, fid, model, ticks=3)
    app.step(fid, sub_ticks=2)
    model.run(2)
    response = app.post(f"/api/fields/{fid}/transducers/byz/quarantine")
    assert response.status_code == 202 and response.json()["status"] == "pending"
    residual = app.audit(fid)["residual_total"]
    held = app.get(f"/api/fields/{fid}/transducers/byz").json()["residuals"]
    assert held and residual != 0.0
    app.step(fid, sub_ticks=2)
    model.run(2)
    assert_cells(app.cells(fid), model.v)
    assert app.audit(fid)["residual_total"] == residual
    model.on_boundary = lambda m: lm.flush_residuals(m, "byz") if m.tick == 4 else None
    app.step(fid, sub_ticks=1)
    model.run(1)
    audit = app.audit(fid)
    assert audit["residual_total"] == 0.0 and conserved(audit)
    assert app.get(f"/api/fields/{fid}/transducers/byz").json()["residuals"] == []
    assert_cells(app.cells(fid), model.v)
    again = app.post(f"/api/fields/{fid}/transducers/byz/quarantine")
    assert again.status_code == 202 and again.json()["status"] == "quarantined"


def test_byzantine_frames_injected_mid_tick(app):
    fid, _ = build(app, consumer=False)
    bad = [{"q": 0, "r": 0, "delta": 1.0}]
    for tick, sub in ((1, 2), (2, 3), (3, 1)):
        assert (
            fault(
                app, fid, tick, sub, {"type": "frame", "transducer": "byz", "deltas": bad}
            ).status_code
            == 201
        )
    app.step(fid, sub_ticks=4 * 3 + 2)  # to (3, 2): the third violation fired at (3, 1)
    assert status(app, fid) == "pending"
    assert app.get(f"/api/fields/{fid}/transducers/byz").json()["violations"] == 3
    app.step(fid, sub_ticks=2)
    assert status(app, fid) == "pending"
    app.step(fid, sub_ticks=1)
    assert status(app, fid) == "quarantined"
    assert conserved(app.audit(fid))


def test_fault_validation(app):
    fid, _ = build(app, consumer=False)
    app.step(fid, sub_ticks=6)  # now at (1, 2)
    assert_error(
        fault(app, fid, 1, 1, {"type": "quarantine", "transducer": "byz"}), 422, "in_the_past"
    )
    assert_error(
        fault(app, fid, 0, 3, {"type": "quarantine", "transducer": "byz"}), 422, "in_the_past"
    )
    assert fault(app, fid, 1, 2, {"type": "quarantine", "transducer": "n1"}).status_code == 201
    assert_error(
        fault(app, fid, 3, 0, {"type": "quarantine", "transducer": "ghost"}), 404, "not_found"
    )
    assert_error(fault(app, fid, 3, 0, {"type": "meteor"}), 422, "invalid_request")
    assert_error(
        fault(app, fid, 3, 4, {"type": "quarantine", "transducer": "n1"}), 422, "invalid_request"
    )
    assert_error(
        fault(app, fid, 3, 0, {"type": "stage_policy", "source": "v +"}), 422, "compile_error"
    )
    assert_error(
        fault(app, fid, 3, 0, {"type": "stage_policy", "source": RULE, "fuel": 2}),
        422,
        "verification_failed",
    )
    listed = app.get(f"/api/fields/{fid}/faults").json()["faults"]
    assert [(f["id"], f["status"]) for f in listed] == [(1, "scheduled")]
    app.step(fid, sub_ticks=1)
    assert app.get(f"/api/fields/{fid}/faults").json()["faults"][0]["status"] == "fired"
    assert status(app, fid, "n1") == "pending"
