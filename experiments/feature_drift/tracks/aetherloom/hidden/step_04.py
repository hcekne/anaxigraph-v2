"""Request 4: .loom rules, static cost, fuel, stack and gates in Arbitrate."""

from conftest import DEFAULT_BOUNDARY, assert_cells, assert_error, lm


def compile_rule(app, source):
    return app.post("/api/policies/compile", json={"source": source})


def clamp(x: float) -> float:
    if x != x:
        return 0.0
    return min(max(x, 0.0), 1.0)


def neighbour(field, cell, d):
    dq, dr = lm.DIRS[d]
    return field.v.get((cell[0] + dq, cell[1] + dr), 0.0)


def nested(depth: int) -> str:
    source = "1"
    for _ in range(depth - 1):
        source = f"1 + ({source})"
    return source


def test_compile_reports_static_fuel_and_stack(app):
    cases = {
        "1": (1, 1),
        "v * 0.5": (3, 2),
        "if(v > 1, 0.5, 1)": (5, 2),
        "min(n0, n1) + abs(-q)  # comment": (7, 2),
        "if(ring >= 2, max(n0, n3) / 2, 1)": (9, 2),
        "-(-tick)": (3, 1),
        "1 < 2 < 3": (5, 2),
        nested(17): (33, 17),
    }
    for source, (fuel, stack) in cases.items():
        response = compile_rule(app, source)
        assert response.status_code == 200, (source, response.text)
        body = response.json()
        assert (body["static_fuel"], body["max_stack"]) == (fuel, stack), source


def test_compile_errors(app):
    for source in (
        "",
        "v +",
        "foo",
        "bar(1)",
        "min(1)",
        "abs(1, 2)",
        "if(1, 2)",
        "__import__('os')",
        "1 ** 2",
        "v; 1",
        "open('x')",
        "(1",
        "1.",
        "v[0]",
    ):
        assert_error(compile_rule(app, source), 422, "compile_error")
    assert_error(compile_rule(app, "1" + " " * 4000), 422, "invalid_request")


def test_gates_scale_flows(app):
    fid = app.field(radius=3, k=0.125)
    start = {(0, 0): 16.0, (2, -1): 5.0, (-3, 3): 7.25, (1, 1): -2.0}
    app.set_cells(fid, start)
    source = "if(ring >= 2, 0.25, min(1, abs(n0 - n3) / 4 + 0.5)) + q / 0"
    response = app.put(f"/api/fields/{fid}/policy", json={"source": source})
    assert response.status_code == 200, response.text
    expected = {"source": source, "fuel": 64, "static_fuel": 18, "max_stack": 3, "exhausted": 0}
    assert {key: response.json().get(key) for key in expected} == expected
    model = lm.Field(3, k=0.125, boundary=DEFAULT_BOUNDARY)
    model.set(start)

    def gate(field, cell):
        if lm.ring(*cell) >= 2:
            return 0.25
        x = min(1.0, abs(neighbour(field, cell, 0) - neighbour(field, cell, 3)) / 4 + 0.5)
        return clamp(x + 0.0)

    model.gate = gate
    app.step(fid, ticks=6)
    model.step(6)
    assert_cells(app.cells(fid), model.v)
    assert app.summary(fid)["sink"] == model.sink()


def test_tick_variable_and_fail_closed_fuel(app):
    fid = app.field(radius=2, k=0.1)
    start = {(0, 0): 3.0, (1, -1): 0.5, (-2, 2): 1.5}
    app.set_cells(fid, start)
    expensive = "if(v > 1, min(min(min(v, n0), min(n1, n2)), 1) * 0.5, if(tick < 2, 0, 1))"
    body = app.put(f"/api/fields/{fid}/policy", json={"source": expensive, "fuel": 10}).json()
    assert (body["static_fuel"], body["fuel"]) == (15, 10)
    model = lm.Field(2, k=0.1, boundary=DEFAULT_BOUNDARY)
    model.set(start)
    exhausted = 0

    def gate(field, cell):
        nonlocal exhausted
        if field.v[cell] > 1:
            exhausted += 1
            return 0.0
        return 0.0 if field.tick < 2 else 1.0

    model.gate = gate
    for _ in range(5):
        app.step(fid)
        model.step()
        assert_cells(app.cells(fid), model.v)
    assert exhausted > 0
    assert app.get(f"/api/fields/{fid}/policy").json()["exhausted"] == exhausted


def test_policy_lifecycle(app):
    fid = app.field(radius=2, k=0.1)
    assert_error(app.get(f"/api/fields/{fid}/policy"), 404, "no_policy")
    assert_error(app.delete(f"/api/fields/{fid}/policy"), 404, "no_policy")
    for fuel in (0, 10001, "x"):
        assert_error(
            app.put(f"/api/fields/{fid}/policy", json={"source": "1", "fuel": fuel}),
            422,
            "invalid_request",
        )
    assert_error(app.put(f"/api/fields/{fid}/policy", json={"source": "v +"}), 422, "compile_error")
    assert_error(
        app.put(f"/api/fields/{fid}/policy", json={"source": nested(17)}), 422, "stack_exceeded"
    )
    assert app.put(f"/api/fields/{fid}/policy", json={"source": nested(16)}).status_code == 200
    assert app.put(f"/api/fields/{fid}/policy", json={"source": "0"}).status_code == 200
    app.set_cells(fid, {(0, 0): 4.0})
    app.step(fid, ticks=2)
    assert app.cells(fid)[(0, 0)] == 4.0
    assert app.delete(f"/api/fields/{fid}/policy").status_code == 204
    assert_error(app.get(f"/api/fields/{fid}/policy"), 404, "no_policy")
    model = lm.Field(2, k=0.1, boundary=DEFAULT_BOUNDARY)
    model.set({(0, 0): 4.0})
    model.tick = 2
    app.step(fid, ticks=3)
    model.step(3)
    assert_cells(app.cells(fid), model.v)
    assert_error(app.put("/api/fields/missing/policy", json={"source": "1"}), 404, "not_found")
