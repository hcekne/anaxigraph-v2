"""Request 12: staged policies, static verification and activation at tick boundaries."""

from conftest import DEFAULT_BOUNDARY, assert_cells, assert_error, lm

START = {(0, 0): 9.0, (2, -1): 3.0, (-1, 2): 1.5}
HALF = "if(v > 2, 0.5, 1)"
FREEZE = "0"


def stage(app, fid, source, fuel=64):
    return app.post(f"/api/fields/{fid}/policy/stage", json={"source": source, "fuel": fuel})


def setup(app):
    fid = app.field(radius=3, k=0.125)
    app.set_cells(fid, START)
    model = lm.Field(3, k=0.125, boundary=DEFAULT_BOUNDARY)
    model.set(START)
    return fid, model


def test_static_verification(app):
    fid, _ = setup(app)
    assert_error(stage(app, fid, "v +"), 422, "compile_error")
    assert_error(stage(app, fid, HALF, fuel=4), 422, "verification_failed")
    deep = "1"
    for _ in range(16):
        deep = f"1 + ({deep})"
    assert_error(stage(app, fid, deep, fuel=1000), 422, "verification_failed")
    for fuel in (0, 10001):
        assert_error(stage(app, fid, HALF, fuel=fuel), 422, "invalid_request")
    assert_error(app.get(f"/api/fields/{fid}/policy/staged"), 404, "no_staged_policy")
    response = stage(app, fid, HALF, fuel=5)
    assert response.status_code == 202
    assert response.json() == {
        "staged": {"source": HALF, "fuel": 5, "static_fuel": 5, "max_stack": 2},
        "activates_at_tick": 0,
    }


def test_activation_waits_for_the_next_boundary(app):
    fid, model = setup(app)
    app.step(fid, ticks=3)
    app.step(fid, sub_ticks=2)
    model.run(4 * 3 + 2)
    assert stage(app, fid, HALF).json()["activates_at_tick"] == 4
    assert_error(app.get(f"/api/fields/{fid}/policy"), 404, "no_policy")
    app.step(fid, sub_ticks=2)  # Arbitrate + Commit of tick 3: still without the rule
    model.run(2)
    assert_cells(app.cells(fid), model.v)
    assert app.get(f"/api/fields/{fid}/policy/staged").status_code == 200
    model.gate = lambda f, c: 0.5 if f.v[c] > 2 else 1.0
    app.step(fid, sub_ticks=1)
    model.run(1)
    assert_error(app.get(f"/api/fields/{fid}/policy/staged"), 404, "no_staged_policy")
    active = app.get(f"/api/fields/{fid}/policy").json()
    assert (active["source"], active["active_since"], active["exhausted"]) == (HALF, 4, 0)
    app.step(fid, ticks=3)
    model.step(3)
    assert_cells(app.cells(fid), model.v)


def test_restaging_replaces_and_staging_at_a_boundary(app):
    fid, model = setup(app)
    app.step(fid, ticks=2)
    model.step(2)
    assert stage(app, fid, FREEZE).json()["activates_at_tick"] == 2
    assert stage(app, fid, HALF).json()["activates_at_tick"] == 2
    assert app.get(f"/api/fields/{fid}/policy/staged").json()["source"] == HALF
    model.gate = lambda f, c: 0.5 if f.v[c] > 2 else 1.0
    app.step(fid, ticks=2)
    model.step(2)
    assert_cells(app.cells(fid), model.v)
    app.step(fid, sub_ticks=1)
    model.run(1)
    assert stage(app, fid, FREEZE).status_code == 202
    assert app.delete(f"/api/fields/{fid}/policy/staged").status_code == 204
    assert_error(app.delete(f"/api/fields/{fid}/policy/staged"), 404, "no_staged_policy")
    app.step(fid, ticks=2)
    model.run(8)
    assert_cells(app.cells(fid), model.v)
    assert app.get(f"/api/fields/{fid}/policy").json()["source"] == HALF


def test_direct_install_only_at_boundaries(app):
    fid, model = setup(app)
    app.step(fid, sub_ticks=1)
    assert_error(app.put(f"/api/fields/{fid}/policy", json={"source": HALF}), 409, "mid_tick")
    app.step(fid, sub_ticks=3)
    response = app.put(f"/api/fields/{fid}/policy", json={"source": HALF})
    assert response.status_code == 200 and response.json()["active_since"] == 1
    app.step(fid, sub_ticks=2)
    assert_error(app.delete(f"/api/fields/{fid}/policy"), 409, "mid_tick")
    app.step(fid, sub_ticks=2)
    assert app.delete(f"/api/fields/{fid}/policy").status_code == 204


def test_staged_rules_are_part_of_checkpoints(app):
    fid, model = setup(app)
    app.step(fid, ticks=2)
    stage(app, fid, FREEZE)
    assert app.post(f"/api/fields/{fid}/checkpoints").status_code == 201
    app.step(fid, ticks=3)
    frozen = app.cells(fid)
    app.step(fid, ticks=1)
    assert app.cells(fid) == frozen
    app.post(f"/api/fields/{fid}/restore", json={"tick": 2})
    assert app.get(f"/api/fields/{fid}/policy/staged").json()["source"] == FREEZE
    assert_error(app.get(f"/api/fields/{fid}/policy"), 404, "no_policy")
    model.step(2)
    assert_cells(app.cells(fid), model.v)
