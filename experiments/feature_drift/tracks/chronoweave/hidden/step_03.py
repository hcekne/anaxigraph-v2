"""Request 3: forks from historical coordinates, minimal causal diff, promotion."""

from conftest import band, expect_error, subset, uid


def test_fork_from_historical_coordinate(app):
    p = uid("k") + ":"
    a1 = app.add(p + "a", 1, 100)
    t1 = app.now
    a2 = app.add(p + "b", 2, 200)
    assert app.retract(a1["id"]).status_code == 200
    name = uid("b-")
    response = app.post(
        "/api/branches", json={"name": name, "from": {"effective_at": 150, "asserted_at": t1}}
    )
    assert response.status_code == 201, response.text
    assert response.json() == {
        "name": name,
        "status": "active",
        "from": {"effective_at": 150, "asserted_at": t1},
    }
    row = next(b for b in app.get("/api/branches").json()["branches"] if b["name"] == name)
    assert row["from"] == {"effective_at": 150, "asserted_at": t1}
    main_row = next(b for b in app.get("/api/branches").json()["branches"] if b["name"] == "main")
    assert main_row["from"] is None
    # as of t1, main knew a1 (not yet retracted) and not a2
    assert app.state(name, prefix=p) == {p + "a": (1, a1["id"])}
    assert app.record(a1["id"], branch=name)["retracted_at"] is None
    expect_error(app.get(f"/api/branches/{name}/assertions/{a2['id']}"), 404, "not_found")
    # the branch clock starts at t1; the horizon is 150
    assert app.write(p + "c", 3, 150, branch=name, asserted_at=t1).status_code == 201
    expect_error(app.write(p + "c", 3, 149, branch=name), 422, "before_fork")
    expect_error(
        app.write(p + "c", 3, 160, branch=name, asserted_at=t1 - 1), 409, "clock_regression"
    )
    # a new branch on the same old coordinate builds on a1 even though main retracted it since
    name2 = app.fork(frm={"effective_at": None, "asserted_at": t1})
    assert app.write(p + "d", 4, 10, branch=name2, parents=[a1["id"]]).status_code == 201
    for bad in (
        {"asserted_at": app.now + 1000},
        {"effective_at": 1},
        {"effective_at": -1, "asserted_at": 1},
        "x",
    ):
        response = app.post("/api/branches", json={"name": uid("b-"), "from": bad})
        expect_error(response, 422, "invalid_request")


def test_minimal_causal_diff(app):
    p = uid("k") + ":"
    limit = app.add(p + "limit", 500, 100)
    owner = app.add(p + "owner", "ann", 300)
    name = app.fork()
    aux = app.add(p + "owner", "ann", 200, branch=name)  # loses to main's owner at 300
    noise = app.add(p + "owner", "bob", 250, branch=name)  # loses too, and nothing builds on it
    hyp = app.add(p + "limit", 900, 150, branch=name, parents=[aux["id"]])
    new_key = app.add(p + "note", "draft", 50, branch=name)
    extra = app.add(p + "extra", True, 10)  # main-only change after the fork
    response = app.get(f"/api/branches/{name}/diff")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["branch"] == name
    changes = [c for c in body["changes"] if c["key"].startswith(p)]
    assert changes == [
        {"key": p + "extra", "main": {"value": True, "assertion_id": extra["id"]}, "branch": None},
        {
            "key": p + "limit",
            "main": {"value": 500, "assertion_id": limit["id"]},
            "branch": {"value": 900, "assertion_id": hyp["id"]},
        },
        {
            "key": p + "note",
            "main": None,
            "branch": {"value": "draft", "assertion_id": new_key["id"]},
        },
    ]
    assert body["assertions"] == [new_key["id"], hyp["id"], aux["id"]]
    assert noise["id"] not in body["assertions"] and owner["id"]
    assert body["conflicts"] == []
    # at effective 120 the hypothetical limit does not exist yet
    early = app.get(f"/api/branches/{name}/diff", params={"effective_at": 120}).json()
    assert [c["key"] for c in early["changes"] if c["key"].startswith(p)] == [
        p + "extra",
        p + "note",
    ]
    assert early["assertions"] == [new_key["id"]]
    # main changes a key the branch changed too -> conflict
    app.add(p + "limit", 700, 120)
    assert app.get(f"/api/branches/{name}/diff").json()["conflicts"] == [p + "limit"]
    expect_error(app.get("/api/branches/main/diff"), 409, "main_only")
    expect_error(app.get(f"/api/branches/{uid('nob-')}/diff"), 404, "not_found")


def test_merge_promotes_at_merge_time(app):
    p = uid("k") + ":"
    base = app.add(p + "a", 1, 100)
    name = app.fork()
    h1 = app.add(p + "b", 2, 200, branch=name, parents=[base["id"]], author="carol", node="n2")
    h2 = app.add(p + "c", 3, 150, branch=name, parents=[h1["id"]])
    before = app.now
    t = app.t()
    response = app.merge(name, asserted_at=t)
    assert response.status_code == 200, response.text
    assert subset(
        response.json(),
        {"branch": name, "asserted_at": t, "promoted": [h2["id"], h1["id"]], "discarded": []},
    )
    copy = app.record(h1["id"])
    assert {
        k: copy[k]
        for k in ("key", "value", "effective_at", "asserted_at", "causal_parents", "author", "node")
    } == {
        "key": p + "b",
        "value": 2,
        "effective_at": 200,
        "asserted_at": t,
        "causal_parents": [base["id"]],
        "author": "carol",
        "node": "n2",
    }
    assert app.state(prefix=p, asserted_at=before) == {p + "a": (1, base["id"])}
    assert app.state(prefix=p) == {
        p + "a": (1, base["id"]),
        p + "b": (2, h1["id"]),
        p + "c": (3, h2["id"]),
    }
    row = next(b for b in app.get("/api/branches").json()["branches"] if b["name"] == name)
    assert row["status"] == "merged"
    expect_error(app.write(p + "d", 1, 1, branch=name), 409, "branch_closed")
    expect_error(app.merge(name), 409, "branch_closed")
    assert app.state(name, prefix=p)[p + "b"] == (2, h1["id"])  # still readable
    # promoted assertions behave like any other on main
    assert app.retract(base["id"]).status_code == 200
    assert app.record(h2["id"])["status"] == "tainted_pending_reconcile"


def test_failed_merges_change_nothing(app):
    p = uid("k") + ":"
    lo = band()
    app.add(p + "x", 1, lo + 500)
    name = app.fork()
    app.add(p + "x", 2, lo + 600, branch=name)
    app.add(p + "y", 2, lo + 600, branch=name)
    app.add(p + "x", 3, lo + 700)  # conflict on x
    snapshot = app.state(prefix=p)
    expect_error(app.merge(name), 409, "merge_conflict")
    clash = app.fork()
    dup = app.add(p + "z", 1, lo + 800, branch=clash)
    app.add(p + "w", 1, lo + 800, branch=clash)
    app.add(p + "z2", 9, lo + 900, id=dup["id"])  # same id appears on main
    expect_error(app.merge(clash), 409, "duplicate_assertion")
    sealed = app.fork()
    app.add(p + "s", 1, lo + 900, branch=sealed)
    app.add(p + "s", 1, lo + 50, branch=sealed)
    app.seal(lo, lo + 100)
    main_clock = app.now
    expect_error(app.merge(sealed), 409, "sealed_interval")
    clean = app.fork()
    app.add(p + "q", 1, lo + 950, branch=clean)
    expect_error(app.merge(clean, asserted_at=main_clock - 1), 409, "clock_regression")
    assert {k: v for k, v in app.state(prefix=p).items() if k != p + "z2"} == snapshot
    assert app.state(prefix=p)[p + "z2"][0] == 9
    for branch in (name, clash, sealed, clean):
        row = next(b for b in app.get("/api/branches").json()["branches"] if b["name"] == branch)
        assert row["status"] == "active"
    expect_error(app.merge("main"), 409, "main_only")
    expect_error(app.merge(uid("nob-")), 404, "not_found")
