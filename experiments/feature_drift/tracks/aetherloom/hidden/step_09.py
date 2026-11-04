"""Request 9: violations, quarantine at the tick boundary, rerouting and dead letters."""

from conftest import DEFAULT_BOUNDARY, assert_cells, assert_error, lm

BAD = [(0, 0), (1, -1), (2, -2), (-2, 2)]


def layout(app):
    fid = app.field(radius=2, k=0.0625)
    app.transducer(fid, "bad", BAD)
    app.transducer(fid, "a", [(1, 0)])
    app.transducer(fid, "b", [(-1, 0), (0, -1)])
    app.transducer(fid, "c", [(2, -1)])
    return fid


def violate(app, fid, tid="bad", cell=(0, 0)):
    assert_error(app.frame(fid, tid, [(cell, 1.0)]), 422, "conservation_violation")


def view(app, fid, tid):
    return app.get(f"/api/fields/{fid}/transducers/{tid}").json()


def cells_of(app, fid, tid):
    return [(c["q"], c["r"]) for c in view(app, fid, tid)["cells"]]


def test_three_violations_within_fifty_ticks(app):
    fid = layout(app)
    violate(app, fid)
    app.step(fid, ticks=10)
    violate(app, fid)
    app.step(fid, ticks=39)
    assert view(app, fid, "bad")["status"] == "active"
    violate(app, fid)
    body = view(app, fid, "bad")
    assert (body["status"], body["violations"]) == ("pending", 3)
    assert app.get(f"/api/fields/{fid}/quarantine").json() == {
        "quarantined": [],
        "pending": ["bad"],
    }
    app.step(fid, sub_ticks=1)
    assert view(app, fid, "bad")["status"] == "quarantined"
    assert app.get(f"/api/fields/{fid}/quarantine").json() == {
        "quarantined": ["bad"],
        "pending": [],
    }


def test_violations_spread_too_far_apart(app):
    fid = layout(app)
    for gap in (30, 30, 10):
        violate(app, fid)
        app.step(fid, ticks=gap)
    assert view(app, fid, "bad")["status"] == "active"  # ticks 0, 30, 60
    violate(app, fid)  # tick 70: 30, 60, 70 lie within (20, 70]
    assert view(app, fid, "bad")["status"] == "pending"


def test_rerouting_to_fallback_neighbours(app):
    fid = layout(app)
    for _ in range(3):
        violate(app, fid)
    app.step(fid, sub_ticks=1)
    assert cells_of(app, fid, "bad") == []
    assert cells_of(app, fid, "a") == [(0, 0), (1, 0)]
    assert cells_of(app, fid, "b") == [(-1, 0), (0, -1)]
    assert cells_of(app, fid, "c") == [(1, -1), (2, -2), (2, -1)]
    assert_error(app.frame(fid, "bad", [((0, 0), 0.0)]), 409, "quarantined")
    r = app.wire(fid, "bad", lm.frame(0, [((0, 0), 0.0)]))
    assert (r["status"], r.get("error")) == ("rejected", "quarantined")
    assert app.wire(fid, "bad", b"garbage")["status"] == "corrupt"
    assert app.frame(fid, "a", [((0, 0), -0.5), ((1, 0), 0.5)]).status_code == 202
    assert_error(app.frame(fid, "a", [((-2, 2), -0.5), ((1, 0), 0.5)]), 422, "foreign_cell")


def test_quarantine_waits_for_the_boundary_and_drops_queued_frames(app):
    fid = layout(app)
    start = {(0, 0): 4.0, (1, -1): 2.0, (1, 0): 1.0}
    app.set_cells(fid, start)
    model = lm.Field(2, k=0.0625, boundary=DEFAULT_BOUNDARY)
    model.set(start)
    good = [((0, 0), -1.0), ((1, -1), 1.0)]
    assert app.frame(fid, "bad", good).status_code == 202
    violate(app, fid)
    violate(app, fid)
    app.step(fid, sub_ticks=2)  # Emit applied `good`; now mid-tick
    model.queue = [good]
    model.run(2)
    violate(app, fid)
    late = [((0, 0), 0.5), ((2, -2), -0.5)]
    assert app.frame(fid, "bad", late).status_code == 202  # still pending: accepted
    app.transducer(fid, "d", [(-1, 1)])
    assert app.frame(fid, "a", [((1, 0), 0.0)]).status_code == 202
    assert view(app, fid, "bad")["status"] == "pending"
    app.step(fid, sub_ticks=2)  # Propagate/Commit of this tick still see the old world
    model.run(2)
    assert view(app, fid, "bad")["status"] == "pending"
    assert_cells(app.cells(fid), model.v)
    app.step(fid, sub_ticks=1)  # next Emit: quarantine first, `late` dead-lettered
    model.queue = [[((1, 0), 0.0)]]
    model.run(1)
    assert view(app, fid, "bad")["status"] == "quarantined"
    assert_cells(app.cells(fid), model.v)
    letters = app.get(f"/api/fields/{fid}/dead-letters").json()["frames"]
    assert [(f["transducer"], f["reason"], f["tick"]) for f in letters] == [
        ("bad", "conservation_violation", 0),
        ("bad", "conservation_violation", 0),
        ("bad", "conservation_violation", 0),
        ("bad", "quarantined", 1),
    ]
    assert letters[0]["deltas"] == [{"q": 0, "r": 0, "delta": 1.0}]
    assert letters[3]["deltas"] == [
        {"q": 0, "r": 0, "delta": 0.5},
        {"q": 2, "r": -2, "delta": -0.5},
    ]


def test_wire_violations_count_too(app):
    fid = layout(app)
    for seq in range(3):
        r = app.wire(fid, "bad", lm.frame(seq, [((0, 0), 2.0), ((1, -1), -1.0)]))
        assert (r["status"], r.get("error")) == ("rejected", "conservation_violation")
    assert view(app, fid, "bad")["status"] == "pending"
    assert view(app, fid, "a")["status"] == "active" and view(app, fid, "a")["violations"] == 0
    letters = app.get(f"/api/fields/{fid}/dead-letters").json()["frames"]
    assert len(letters) == 3 and all(f["reason"] == "conservation_violation" for f in letters)
