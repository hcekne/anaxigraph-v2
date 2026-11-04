"""Request 13: per-plane conservation auditors in the audit report."""

import math

from conftest import assert_error, lm, near

NAMES = ["charge", "entropy_cap", "heat_sink", "mass", "momentum", "token_quota"]
PLANES = [{"name": "heat", "k": 0.1}, {"name": "ion", "k": 0.05}]


def configure(app, fid, plane, auditors):
    return app.put(f"/api/fields/{fid}/planes/{plane}/auditors", json={"auditors": auditors})


def expected_values(model, index, residual=0.0):
    v = model.values[index]
    cells = model.cells
    total = 0.0
    for c in cells:
        total = total + v[c]
    mq = sum(c[0] * v[c] for c in cells)
    mr = sum(c[1] * v[c] for c in cells)
    positive = [v[c] for c in cells if v[c] > 0]
    s = sum(positive)
    entropy = -sum((x / s) * math.log(x / s) for x in positive) if positive else 0.0
    return {
        "mass": total + model.sinks[index] + residual,
        "momentum": max(abs(mq), abs(mr)),
        "charge": total,
        "entropy_cap": entropy,
        "token_quota": max(v[c] for c in cells),
        "heat_sink": model.sinks[index],
    }


def make(app):
    fid = app.field(radius=3, planes=PLANES, coupling=[[0, 0.0625], [0, 0]], boundary="absorbing")
    heat = {(0, 0): 6.0, (3, -1): 2.0, (-2, 2): 1.0}
    ion = {(1, 1): 3.0, (-1, -1): -3.0}
    app.set_cells(fid, heat, plane="heat")
    app.set_cells(fid, ion, plane="ion")
    model = lm.Field(
        3,
        planes=[("heat", 0.1), ("ion", 0.05)],
        coupling=[[0, 0.0625], [0, 0]],
        boundary="absorbing",
    )
    model.set(heat, 0)
    model.set(ion, 1)
    return fid, model


def test_catalogue(app):
    names = [a["name"] for a in app.get("/api/auditors").json()["auditors"]]
    assert [n for n in names if n in NAMES] == NAMES and names == sorted(names)


def test_configuration_and_validation(app):
    fid, _ = make(app)
    response = configure(
        app,
        fid,
        "heat",
        [{"name": "mass", "params": {"expected": 9.0}}, {"name": "charge", "params": {}}],
    )
    assert response.status_code == 200
    assert response.json() == {
        "plane": "heat",
        "auditors": [
            {"name": "mass", "params": {"expected": 9.0, "tolerance": 1e-9}},
            {"name": "charge", "params": {"max_imbalance": 1e-9}},
        ],
    }
    assert app.get(f"/api/fields/{fid}/planes/heat/auditors").json() == response.json()
    assert app.get(f"/api/fields/{fid}/planes/ion/auditors").json() == {
        "plane": "ion",
        "auditors": [],
    }
    assert_error(configure(app, fid, "nope", []), 404, "not_found")
    assert_error(
        configure(app, fid, "heat", [{"name": "virial", "params": {}}]), 422, "unknown_auditor"
    )
    bad = [
        [{"name": "mass", "params": {}}],
        [{"name": "mass", "params": {"expected": "x"}}],
        [{"name": "mass", "params": {"expected": 1.0, "wobble": 1}}],
        [{"name": "heat_sink", "params": {"max": -1.0}}],
        [{"name": "charge", "params": {}}, {"name": "charge", "params": {}}],
    ]
    for auditors in bad:
        assert_error(configure(app, fid, "heat", auditors), 422, "invalid_request")
    assert len(app.get(f"/api/fields/{fid}/planes/heat/auditors").json()["auditors"]) == 2


def test_auditor_values_follow_the_definitions(app):
    fid, model = make(app)
    everything = [
        {"name": "mass", "params": {"expected": 9.0}},
        {"name": "momentum", "params": {"max": 100.0}},
        {"name": "charge", "params": {"max_imbalance": 1.0}},
        {"name": "entropy_cap", "params": {"max": 5.0}},
        {"name": "token_quota", "params": {"max_cell": 7.0}},
        {"name": "heat_sink", "params": {"max": 10.0}},
    ]
    configure(app, fid, "heat", everything)
    configure(app, fid, "ion", list(reversed(everything)))
    app.step(fid, ticks=4)
    model.step(4)
    report = app.audit(fid)["auditors"]
    order = [(r["plane"], r["name"]) for r in report]
    names = [a["name"] for a in everything]
    assert order == [("heat", n) for n in names] + [("ion", n) for n in reversed(names)]
    for row in report:
        index = 0 if row["plane"] == "heat" else 1
        assert near(row["value"], expected_values(model, index)[row["name"]]), row


def test_ok_flags_and_summary(app):
    fid, model = make(app)
    app.step(fid, ticks=3)
    model.step(3)
    values = expected_values(model, 1)
    configure(
        app,
        fid,
        "ion",
        [
            {"name": "charge", "params": {"max_imbalance": 1e-6}},
            {"name": "token_quota", "params": {"max_cell": values["token_quota"] * 0.99}},
            {"name": "heat_sink", "params": {"max": 0.0}},
        ],
    )
    audit = app.audit(fid)
    flags = {r["name"]: r["ok"] for r in audit["auditors"]}
    assert flags == {
        "charge": values["charge"] <= 1e-6 and values["charge"] >= -1e-6,
        "token_quota": False,
        "heat_sink": values["heat_sink"] <= 0.0,
    }
    assert audit["auditors_ok"] is False and audit["ok"] is True
    configure(
        app,
        fid,
        "ion",
        [{"name": "mass", "params": {"expected": values["mass"], "tolerance": 1e-6}}],
    )
    audit = app.audit(fid)
    assert audit["auditors_ok"] is True and [r["ok"] for r in audit["auditors"]] == [True]
    configure(app, fid, "ion", [])
    assert app.audit(fid)["auditors"] == [] and app.audit(fid)["auditors_ok"] is True


def test_mass_includes_residuals_and_survives_restart(fresh_app):
    app = fresh_app
    fid = app.field(radius=2, k=0.1)
    app.transducer(fid, "edge", [(0, 0), (1, 0)])
    app.put(f"/api/fields/{fid}/transducers/edge/bridge", json={"bits": 16, "step": 0.25})
    app.set_cells(fid, {(0, 0): 2.0})
    app.frame(fid, "edge", [((0, 0), 0.1), ((1, 0), -0.1)])
    configure(
        app, fid, "scalar", [{"name": "mass", "params": {"expected": 2.0, "tolerance": 1e-12}}]
    )
    app.step(fid, ticks=2)
    app.restart()
    audit = app.audit(fid)
    assert audit["auditors"] == [
        {"plane": "scalar", "name": "mass", "value": audit["auditors"][0]["value"], "ok": True}
    ]
    assert near(audit["auditors"][0]["value"], 2.0, 1e-12)
