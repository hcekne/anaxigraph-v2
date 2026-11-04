"""Request 6: pluggable merge conflict resolvers."""

from conftest import expect_error, uid


def scenario(app, p):
    """main: k=1 at 10. Branch forked. Returns the branch name."""
    app.add(p + "k", 1, 10)
    return app.fork()


def test_listing_and_validation(app):
    body = app.get("/api/resolvers").json()
    names = [r["name"] for r in body["resolvers"]]
    assert {"causal_depth_priority", "last_assertion_wins", "quorum_witness"} <= set(names)
    assert names == sorted(names)
    p = uid("k") + ":"
    name = scenario(app, p)
    app.add(p + "k", 2, 20, branch=name)
    app.add(p + "k", 3, 30)
    expect_error(app.merge(name), 409, "merge_conflict")
    expect_error(app.merge(name, resolver="coin_flip"), 422, "unknown_resolver")
    expect_error(app.merge(name, resolver="quorum_witness"), 422, "invalid_request")
    expect_error(
        app.merge(name, resolver="quorum_witness", options={"quorum": 0}), 422, "invalid_request"
    )
    row = next(b for b in app.get("/api/branches").json()["branches"] if b["name"] == name)
    assert row["status"] == "active"
    clean = app.fork()
    free = app.add(p + "free", 1, 1, branch=clean)
    response = app.merge(clean, resolver="last_assertion_wins")
    assert response.status_code == 200, response.text
    assert response.json()["resolutions"] == [] and response.json()["promoted"] == [free["id"]]


def test_last_assertion_wins(app):
    p = uid("k") + ":"
    name = scenario(app, p)
    app.add(p + "k", 3, 30)  # main side first...
    mine = app.add(p + "k", 2, 20, branch=name)  # ...branch asserted later: branch wins
    response = app.merge(name, resolver="last_assertion_wins")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["promoted"] == [mine["id"]] and body["discarded"] == []
    assert body["resolutions"] == [{"key": p + "k", "winner": "branch"}]
    # main candidate later: the branch side is discarded with everything built on it
    q = uid("k") + ":"
    name = scenario(app, q)
    lost = app.add(q + "k", 2, 20, branch=name)
    child = app.add(q + "x", 1, 5, branch=name, parents=[lost["id"]])
    free = app.add(q + "y", 1, 50, branch=name)
    app.add(q + "z", 8, 60)
    also = app.add(q + "z", 9, 60, branch=name)
    main_k = app.add(q + "k", 3, 30)
    body = app.merge(name, resolver="last_assertion_wins").json()
    assert body["resolutions"] == [
        {"key": q + "k", "winner": "main"},
        {"key": q + "z", "winner": "branch"},
    ]
    assert body["discarded"] == [child["id"], lost["id"]]
    assert body["promoted"] == [free["id"], also["id"]]
    assert app.state(prefix=q) == {
        q + "k": (3, main_k["id"]),
        q + "y": (1, free["id"]),
        q + "z": (9, also["id"]),
    }


def test_causal_depth_priority(app):
    p = uid("k") + ":"
    root = app.add(p + "root", 1, 1)
    name = app.fork()
    mid = app.add(p + "mid", 1, 2, parents=[root["id"]])
    deep = app.add(p + "k", "main", 10, parents=[mid["id"]])  # depth 2
    shallow = app.add(p + "k", "branch", 20, branch=name)  # depth 0, but asserted later
    body = app.merge(name, resolver="causal_depth_priority").json()
    assert body["resolutions"] == [{"key": p + "k", "winner": "main"}]
    assert body["discarded"] == [shallow["id"]]
    # the branch candidate is deeper, through inherited parents
    q = uid("k") + ":"
    base = app.add(q + "base", 1, 1)
    name = app.fork()
    b1 = app.add(q + "b1", 1, 2, branch=name, parents=[base["id"]])
    b2 = app.add(q + "k", "branch", 3, branch=name, parents=[b1["id"]])
    app.add(q + "k", "main", 4)
    body = app.merge(name, resolver="causal_depth_priority").json()
    assert body["resolutions"] == [{"key": q + "k", "winner": "branch"}]
    assert body["promoted"] == [b1["id"], b2["id"]]
    # equal depth falls back to the later assertion
    r = uid("k") + ":"
    app.add(r + "k", 0, 1)
    name = app.fork()
    app.add(r + "k", "main", 5)
    later = app.add(r + "k", "branch", 4, branch=name)
    body = app.merge(name, resolver="causal_depth_priority").json()
    assert body["resolutions"] == [{"key": r + "k", "winner": "branch"}] and body["promoted"] == [
        later["id"]
    ]
    assert deep["id"]


def test_quorum_witness(app):
    def build():
        p = uid("k") + ":"
        name = scenario(app, p)
        app.add(p + "k", "main", 40, node="m1")
        app.add(p + "k", "no", 5, branch=name, node="n3")  # different value: not a witness
        a = app.add(p + "k", "yes", 20, branch=name, node="n1")
        b = app.add(p + "k", "yes", 21, branch=name, node="n2")
        c = app.add(p + "k", "yes", 22, branch=name, node="n2")  # the candidate
        return p, name, [a, b, c]

    p, name, winners = build()
    body = app.merge(name, resolver="quorum_witness", options={"quorum": 2}).json()
    assert body["resolutions"] == [{"key": p + "k", "winner": "branch"}]
    assert {w["id"] for w in winners} <= set(body["promoted"])
    assert app.state(prefix=p)[p + "k"][0] == "main"  # main's value still wins at effective 40
    p, name, _ = build()
    body = app.merge(name, resolver="quorum_witness", options={"quorum": 3}).json()
    assert body["resolutions"] == [{"key": p + "k", "winner": "main"}] and body["promoted"] == []
    assert len(body["discarded"]) == 4
