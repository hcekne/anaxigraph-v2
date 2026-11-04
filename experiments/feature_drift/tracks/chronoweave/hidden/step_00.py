"""Request 0: assertions, retraction, bitemporal materialization, frozen branches, persistence."""

from conftest import P1, expect_error, subset, uid


def test_assertion_record_roundtrip(app):
    p = uid("k") + ":"
    root = app.add(p + "acct", {"open": True}, 100, author="bob", node="node-a")
    t = app.t()
    body = {
        "id": uid("c-"),
        "key": p + "limit",
        "value": [1, "two", None],
        "effective_at": 150,
        "asserted_at": t,
        "causal_parents": [root["id"]],
        "author": "alice",
        "node": "node-b",
    }
    response = app.post("/api/branches/main/assertions", json=body)
    assert response.status_code == 201, response.text
    assert subset(response.json(), {**body, "retracted_at": None})
    assert subset(app.record(body["id"]), {**body, "retracted_at": None})
    body2 = {k: v for k, v in body.items() if k not in ("causal_parents", "node")}
    body2["id"] = uid("d-")
    body2["asserted_at"] = app.t()
    created = app.post("/api/branches/main/assertions", json=body2).json()
    assert created["causal_parents"] == [] and created["node"] == "local"
    expect_error(app.get(f"/api/branches/main/assertions/{uid('none-')}"), 404, "not_found")


def test_validation_and_write_rules(app):
    p = uid("k") + ":"
    base = app.add(p + "a", 1, 10)
    good = {"key": p + "b", "value": 2, "effective_at": 10, "author": "alice"}

    def post(**changes):
        body = {"id": uid("v-"), **good, "asserted_at": app.t(), **changes}
        body = {k: v for k, v in body.items() if v is not ...}
        return app.post("/api/branches/main/assertions", json=body)

    for bad in (
        {"id": "-starts-with-dash"},
        {"id": "x" * 65},
        {"key": ""},
        {"key": "k" * 201},
        {"value": ...},
        {"effective_at": -1},
        {"effective_at": True},
        {"effective_at": "10"},
        {"asserted_at": 1.5},
        {"author": ""},
        {"author": ...},
        {"node": ""},
        {"causal_parents": "nope"},
        {"causal_parents": [base["id"], base["id"]]},
    ):
        expect_error(post(**bad), 422, "invalid_request")
    ok = post(value=None)
    assert ok.status_code == 201 and ok.json()["value"] is None
    clock = ok.json()["asserted_at"]
    expect_error(post(causal_parents=[uid("ghost-")]), 422, "unknown_parent")
    expect_error(post(id=base["id"]), 409, "duplicate_assertion")
    expect_error(post(asserted_at=clock - 1), 409, "clock_regression")
    assert post(asserted_at=clock).status_code == 201  # equal to the clock is fine


def test_bitemporal_materialization(app):
    p = uid("k") + ":"
    t1 = app.t()
    v1 = app.add(p + "limit", 100, 1000, asserted_at=t1)
    t2 = app.t()
    v2 = app.add(p + "limit", 200, 2000, asserted_at=t2)
    t3 = app.t()
    fix = app.add(p + "limit", 150, 1000, asserted_at=t3)  # correction of the 1000 value
    other = app.add(p + "owner", "ann", 500)

    assert app.state(prefix=p, effective_at=1500, asserted_at=t2) == {p + "limit": (100, v1["id"])}
    assert app.state(prefix=p, effective_at=1500, asserted_at=t3) == {p + "limit": (150, fix["id"])}
    assert app.state(prefix=p, effective_at=2500, asserted_at=t3)[p + "limit"] == (200, v2["id"])
    assert app.state(prefix=p, effective_at=999, asserted_at=t3) == {}
    assert app.state(prefix=p, asserted_at=t1 - 1) == {}
    assert app.state(prefix=p) == {
        p + "limit": (200, v2["id"]),
        p + "owner": ("ann", other["id"]),
    }
    response = app.get("/api/branches/main/state", params={"prefix": p, "effective_at": 1500})
    body = response.json()
    assert body["branch"] == "main" and body["effective_at"] == 1500
    assert body["asserted_at"] is None
    assert [e["key"] for e in body["entries"]] == [p + "limit", p + "owner"]


def test_tie_break(app):
    """Same key, same effective and asserted time: the later record wins (until P1 changes
    the rule; step 7 checks the new rule, so here only consistency is checked after it)."""
    p = uid("k") + ":"
    t = app.t()
    first = app.add(p + "x", "first", 50, asserted_at=t, node="node-a")
    second = app.add(p + "x", "second", 50, asserted_at=t, node="node-b")
    listed = app.get("/api/branches/main/assertions", params={"key": p + "x"}).json()
    order = [a["id"] for a in listed["assertions"]]
    assert sorted(order) == sorted([first["id"], second["id"]])
    winner = app.state(prefix=p)[p + "x"]
    assert winner[1] == order[-1]
    if not P1:
        assert order == [first["id"], second["id"]] and winner == ("second", second["id"])


def test_retraction_is_a_tombstone(app):
    p = uid("k") + ":"
    old = app.add(p + "rate", 5, 100)
    new = app.add(p + "rate", 7, 200)
    t_before = app.now
    t_retract = app.t()
    response = app.retract(new["id"], t_retract, author="bob")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == new["id"] and response.json()["retracted_at"] == t_retract
    assert app.state(prefix=p, asserted_at=t_before)[p + "rate"] == (7, new["id"])
    assert app.state(prefix=p)[p + "rate"] == (5, old["id"])
    assert app.record(new["id"])["retracted_at"] == t_retract
    listing = app.get("/api/branches/main/assertions", params={"prefix": p}).json()["assertions"]
    assert [a["id"] for a in listing] == [old["id"], new["id"]]
    expect_error(app.retract(new["id"]), 409, "already_retracted")
    expect_error(app.retract(uid("nope-")), 404, "not_found")
    expect_error(app.retract(old["id"], asserted_at=t_retract - 1), 409, "clock_regression")
    response = app.write(p + "child", 1, 300, parents=[new["id"]])
    expect_error(response, 409, "parent_retracted")


def test_branches_are_frozen_copies(app):
    p = uid("k") + ":"
    a = app.add(p + "a", 1, 10)
    gone = app.add(p + "g", 1, 10)
    assert app.retract(gone["id"]).status_code == 200
    name = app.fork()
    branches = app.get("/api/branches").json()["branches"]
    names = [b["name"] for b in branches]
    assert names == sorted(names) and "main" in names and name in names
    assert {"name": name, "status": "active"}.items() <= next(
        b for b in branches if b["name"] == name
    ).items()
    expect_error(app.post("/api/branches", json={"name": name}), 409, "conflict")
    expect_error(app.post("/api/branches", json={"name": "main"}), 409, "conflict")
    for bad in ("Upper", "", "x" * 41, "a_b"):
        expect_error(app.post("/api/branches", json={"name": bad}), 422, "invalid_request")

    later = app.add(p + "later", 2, 10)  # main only
    hyp = app.add(p + "a", 99, 20, branch=name, parents=[a["id"]])  # branch only
    assert app.state(name, prefix=p) == {p + "a": (99, hyp["id"])}
    assert app.state(prefix=p) == {p + "a": (1, a["id"]), p + "later": (2, later["id"])}
    expect_error(app.get(f"/api/branches/{name}/assertions/{later['id']}"), 404, "not_found")
    expect_error(app.get(f"/api/branches/main/assertions/{hyp['id']}"), 404, "not_found")
    expect_error(app.write(p + "z", 1, 1, branch=name, id=a["id"]), 409, "duplicate_assertion")
    expect_error(
        app.write(p + "z", 1, 1, branch=name, parents=[gone["id"]]), 409, "parent_retracted"
    )
    expect_error(
        app.write(p + "z", 1, 1, branch=name, parents=[later["id"]]), 422, "unknown_parent"
    )
    expect_error(app.retract(hyp["id"]), 404, "not_found")
    response = app.post(
        f"/api/branches/{name}/assertions/{hyp['id']}/retract", json={"asserted_at": app.t()}
    )
    expect_error(response, 409, "main_only")
    missing = uid("nob-")
    expect_error(app.get(f"/api/branches/{missing}/state"), 404, "not_found")
    expect_error(app.write(p + "z", 1, 1, branch=missing), 404, "not_found")
    # a new branch's clock starts at main's clock
    app.add(p + "a", 2, 10)
    clock = app.now
    name = app.fork()
    expect_error(
        app.write(p + "b", 1, 10, branch=name, asserted_at=clock - 1), 409, "clock_regression"
    )
    assert app.write(p + "b", 1, 10, branch=name, asserted_at=clock).status_code == 201


def test_everything_survives_restart(fresh_app):
    app = fresh_app
    p = "k:"
    a = app.add(p + "a", {"n": 1}, 10)
    b = app.add(p + "a", {"n": 2}, 20, parents=[a["id"]])
    t_mid = app.now
    assert app.retract(b["id"]).status_code == 200
    name = app.fork()
    h = app.add(p + "h", "hyp", 30, branch=name)
    before = (
        app.state(prefix=p),
        app.state(prefix=p, asserted_at=t_mid),
        app.state(name, prefix=p),
        app.get("/api/branches").json(),
        app.record(b["id"]),
    )
    app.restart()
    after = (
        app.state(prefix=p),
        app.state(prefix=p, asserted_at=t_mid),
        app.state(name, prefix=p),
        app.get("/api/branches").json(),
        app.record(b["id"]),
    )
    assert before == after
    assert after[2][p + "h"] == ("hyp", h["id"])
    expect_error(app.write(p + "c", 1, 1, asserted_at=t_mid), 409, "clock_regression")
