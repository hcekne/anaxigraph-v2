"""Request 7 (fault probe P1): ties break by BLAKE2b payload digest everywhere."""

from conftest import payload_digest, uid


def body(app, key, value, effective_at, asserted_at, node, parents=(), ident=None):
    return {
        "id": ident or uid("a-"),
        "key": key,
        "value": value,
        "effective_at": effective_at,
        "asserted_at": asserted_at,
        "causal_parents": list(parents),
        "author": "alice",
        "node": node,
    }


def inverted_pair(app, key, effective_at, asserted_at, values=("one", "two")):
    """Two tied bodies where the first-recorded one has the larger digest, so record order
    and digest order disagree."""
    while True:
        first = body(app, key, values[0], effective_at, asserted_at, "node-a")
        second = body(app, key, values[1], effective_at, asserted_at, "node-b")
        if payload_digest(first) > payload_digest(second):
            return first, second


def post(app, data, branch="main"):
    response = app.post(f"/api/branches/{branch}/assertions", json=data)
    assert response.status_code == 201, response.text
    return response.json()


def test_records_carry_payload_digest(app):
    p = uid("k") + ":"
    parent = app.add(p + "a", {"x": [1, 2]}, 5, node="n1")
    child = app.add(p + "b", "ü", 6, parents=[parent["id"]], node="n2")
    for record in (parent, child):
        assert app.record(record["id"])["digest"] == payload_digest(record)
    listed = app.get("/api/branches/main/assertions", params={"prefix": p}).json()["assertions"]
    assert [r["digest"] for r in listed] == [payload_digest(parent), payload_digest(child)]
    name = app.fork()
    hyp = app.add(p + "c", 1, 7, branch=name)
    assert app.record(hyp["id"], branch=name)["digest"] == payload_digest(hyp)
    assert app.merge(name).status_code == 200
    assert app.record(hyp["id"])["digest"] == payload_digest(hyp)  # the promoted copy keeps it


def test_state_and_listing_use_digest(app):
    p = uid("k") + ":"
    first, second = inverted_pair(app, p + "x", 100, app.t())
    post(app, first)
    post(app, second)
    assert app.state(prefix=p)[p + "x"] == (first["value"], first["id"])
    listed = app.get("/api/branches/main/assertions", params={"key": p + "x"}).json()["assertions"]
    assert [r["id"] for r in listed] == [second["id"], first["id"]]
    status = app.get("/api/branches/main/state", params={"prefix": p}).json()
    assert status["entries"][0]["assertion_id"] == first["id"]


def test_diff_uses_digest(app):
    p = uid("k") + ":"
    app.add(p + "x", "base", 1)
    name = app.fork()
    first, second = inverted_pair(app, p + "x", 100, app.t())
    post(app, first, name)
    post(app, second, name)
    assert app.state(name, prefix=p)[p + "x"] == (first["value"], first["id"])
    diff = app.get(f"/api/branches/{name}/diff").json()
    change = next(c for c in diff["changes"] if c["key"] == p + "x")
    assert change["branch"] == {"value": first["value"], "assertion_id": first["id"]}
    assert diff["assertions"] == [first["id"]]


def test_merge_tie_and_promotion_order_use_digest(app):
    # last_assertion_wins on a full tie between main and branch: the larger digest wins,
    # whichever side was recorded first
    for first_side in ("branch", "main"):
        p = uid("k") + ":"
        app.add(p + "k", "base", 1)
        name = app.fork()
        first, second = inverted_pair(app, p + "k", 50, app.t(), (first_side, "other"))
        post(app, first, name if first_side == "branch" else "main")
        post(app, second, "main" if first_side == "branch" else name)
        response = app.merge(name, resolver="last_assertion_wins")
        assert response.status_code == 200, response.text
        assert response.json()["resolutions"] == [{"key": p + "k", "winner": first_side}]
    # promoted copies tie on (effective_at, asserted_at): listed in digest order
    p = uid("k") + ":"
    name = app.fork()
    early, late = inverted_pair(app, p + "y", 70, app.t())
    late["asserted_at"] = early["asserted_at"] + 5
    post(app, early, name)
    app.t(10)
    post(app, late, name)
    response = app.merge(name)
    assert response.status_code == 200, response.text
    assert response.json()["promoted"] == [late["id"], early["id"]]
    assert app.state(prefix=p)[p + "y"][1] == early["id"]
