"""Request 15: sectors, halos, exchange accounting and order-independent parallel stepping."""

from conftest import assert_cells, assert_error, lm, near


def seeded(radius):
    return {c: ((i * 37) % 23) / 4.0 for i, c in enumerate(lm.grid(radius)) if i % 4 == 0}


def sectors(app, fid):
    response = app.get(f"/api/fields/{fid}/sectors")
    assert response.status_code == 200, response.text
    return response.json()


def test_sector_layout_and_halos(app):
    fid = app.field(radius=20, k=0.1)
    body = sectors(app, fid)
    assert body["sector_size"] == 16 and app.summary(fid)["sector_size"] == 16
    expected = lm.sectors(20, 16)
    assert [(s["sq"], s["sr"]) for s in body["sectors"]] == list(expected)
    for row in body["sectors"]:
        model = expected[(row["sq"], row["sr"])]
        assert (row["cells"], row["halo"]) == (len(model["cells"]), len(model["halo"])), row
        assert row["total"] == 0.0 and row["exchange"] == 0.0
    small = app.field(radius=3, k=0.1, sector_size=2)
    expected = lm.sectors(3, 2)
    got = {(s["sq"], s["sr"]): (s["cells"], s["halo"]) for s in sectors(app, small)["sectors"]}
    assert got == {key: (len(v["cells"]), len(v["halo"])) for key, v in expected.items()}
    for size in (1, 65, "x"):
        payload = {"id": "bad-sector", "radius": 3, "k": 0.1, "sector_size": size}
        assert_error(app.post("/api/fields", json=payload), 422, "invalid_request")


def test_results_do_not_depend_on_workers_or_order(app):
    start = seeded(18)
    plain = app.field(radius=18, k=0.15, sector_size=8, boundary="absorbing")
    sharded = app.field(radius=18, k=0.15, sector_size=8, boundary="absorbing")
    for fid in (plain, sharded):
        app.set_cells(fid, start)
        app.put(f"/api/fields/{fid}/policy", json={"source": "if(v > 3, 0.5, 1)"})
    order = [[s["sq"], s["sr"]] for s in sectors(app, sharded)["sectors"]]
    app.step(plain, ticks=3)
    app.step(sharded, ticks=1, workers=4, sector_order=list(reversed(order)))
    app.step(sharded, sub_ticks=6, workers=8, sector_order=order[1::2] + order[0::2])
    app.step(sharded, sub_ticks=2, workers=3)
    assert app.cells(sharded) == app.cells(plain)
    assert app.summary(sharded)["sink"] == app.summary(plain)["sink"]
    model = lm.Field(18, k=0.15, boundary="absorbing")
    model.set(start)
    model.gate = lambda f, c: 0.5 if f.v[c] > 3 else 1.0
    model.step(3)
    assert_cells(app.cells(plain), model.v)
    assert app.summary(plain)["sink"] == model.sink()


def test_exchange_and_totals(app):
    for boundary in ("reflective", "absorbing"):
        fid = app.field(
            radius=6,
            k=0.125,
            sector_size=4,
            boundary=boundary,
            planes=[{"name": "a", "k": 0.125}, {"name": "b", "k": 0.0625}],
            coupling=[[0, 0.25], [0.125, 0]],
        )
        start = seeded(6)
        app.set_cells(fid, start, plane="a")
        app.set_cells(fid, {(0, 0): 3.0, (6, -6): 1.0}, plane="b")
        model = lm.Field(
            6,
            planes=[("a", 0.125), ("b", 0.0625)],
            coupling=[[0, 0.25], [0.125, 0]],
            boundary=boundary,
        )
        model.set(start, 0)
        model.set({(0, 0): 3.0, (6, -6): 1.0}, 1)
        app.step(fid, ticks=2, workers=2)
        model.step(2)
        expected = lm.exchange(model, 4)
        body = sectors(app, fid)["sectors"]
        total = 0.0
        for row in body:
            key = (row["sq"], row["sr"])
            assert near(row["exchange"], expected.get(key, 0.0), 1e-9), (boundary, key, row)
            cells_total = 0.0
            for c in lm.sectors(6, 4)[key]["cells"]:
                cells_total = cells_total + model.values[0][c] + model.values[1][c]
            assert near(row["total"], cells_total, 1e-12)
            total += row["exchange"]
        assert abs(total) < 1e-9


def test_step_parameter_validation(app):
    fid = app.field(radius=3, k=0.1, sector_size=4)
    order = [[s["sq"], s["sr"]] for s in sectors(app, fid)["sectors"]]
    bad = [
        {"workers": 0},
        {"workers": 9},
        {"workers": "2"},
        {"sector_order": order[1:]},
        {"sector_order": order + order[:1]},
        {"sector_order": order[:-1] + [[99, 99]]},
        {"sector_order": "all"},
    ]
    for body in bad:
        assert_error(app.post(f"/api/fields/{fid}/step", json=body), 422, "invalid_request")
    assert app.summary(fid)["tick"] == 0
    assert app.step(fid, sector_order=order[::-1], workers=2)["tick"] == 1
