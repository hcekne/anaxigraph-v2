"""Request 1: taint cascade on retraction, checkpoint insulation, reconcile, repeatable reads."""

from conftest import expect_error, uid


def status(app, ident, **params):
    record = app.record(ident, **params)
    return record["status"], record["tainted_by"]


def test_retraction_taints_descendants_only(app):
    p = uid("k") + ":"
    a = app.add(p + "a", 1, 100)
    b = app.add(p + "b", 2, 200, parents=[a["id"]])
    c = app.add(p + "c", 3, 300, parents=[b["id"]])
    d = app.add(p + "d", 4, 250, parents=[b["id"]])
    e = app.add(p + "e", 5, 50)
    before = app.now
    assert app.retract(a["id"]).status_code == 200
    assert status(app, a["id"])[0] == "retracted"
    for x in (b, c, d):
        assert status(app, x["id"]) == ("tainted_pending_reconcile", [a["id"]])
    assert status(app, e["id"]) == ("active", [])
    # the earlier view does not change
    for x in (a, b, c, d, e):
        assert status(app, x["id"], asserted_at=before) == ("active", [])
    assert app.record(a["id"], asserted_at=before)["retracted_at"] is None
    entries = app.get("/api/branches/main/state", params={"prefix": p}).json()["entries"]
    assert {x["key"]: x["status"] for x in entries} == {
        p + "b": "tainted_pending_reconcile",
        p + "c": "tainted_pending_reconcile",
        p + "d": "tainted_pending_reconcile",
        p + "e": "active",
    }
    old = app.get("/api/branches/main/state", params={"prefix": p, "asserted_at": before})
    assert {x["status"] for x in old.json()["entries"]} == {"active"}


def test_checkpoint_insulates_and_paths_count(app):
    p = uid("k") + ":"
    a = app.add(p + "a", 1, 100)
    b = app.add(p + "b", 1, 110, parents=[a["id"]])
    c = app.add(p + "c", 1, 120, parents=[a["id"]])
    d = app.add(p + "d", 1, 130, parents=[b["id"], c["id"]])  # diamond a->b,c->d
    only_b = app.add(p + "e", 1, 140, parents=[b["id"]])
    t = app.t()
    response = app.post(
        "/api/branches/main/checkpoints", json={"assertion_id": b["id"], "asserted_at": t}
    )
    assert response.status_code == 201, response.text
    assert response.json() == {"assertion_id": b["id"], "asserted_at": t}
    assert app.retract(a["id"]).status_code == 200
    assert status(app, b["id"]) == ("active", [])
    assert status(app, only_b["id"]) == ("active", [])
    assert status(app, c["id"]) == ("tainted_pending_reconcile", [a["id"]])
    assert status(app, d["id"]) == ("tainted_pending_reconcile", [a["id"]])  # via c, once
    # a checkpoint after the retraction does not clear existing taint
    late = app.post(
        "/api/branches/main/checkpoints", json={"assertion_id": c["id"], "asserted_at": app.t()}
    )
    assert late.status_code == 201
    assert status(app, c["id"]) == ("tainted_pending_reconcile", [a["id"]])


def test_checkpoint_errors(app):
    p = uid("k") + ":"
    a = app.add(p + "a", 1, 100)
    gone = app.add(p + "g", 1, 100)
    assert app.retract(gone["id"]).status_code == 200

    def cp(ident, at=None, branch="main"):
        body = {"assertion_id": ident, "asserted_at": at if at is not None else app.t()}
        return app.post(f"/api/branches/{branch}/checkpoints", json=body)

    assert cp(a["id"]).status_code == 201
    expect_error(cp(a["id"]), 409, "conflict")
    expect_error(cp(uid("ghost-")), 404, "not_found")
    expect_error(cp(gone["id"]), 409, "already_retracted")
    other = app.add(p + "o", 1, 100)
    app.add(p + "later", 1, 100)  # moves the clock past other's own time; other stays visible
    expect_error(cp(other["id"], at=other["asserted_at"]), 409, "clock_regression")
    name = app.fork()
    expect_error(cp(other["id"], branch=name), 409, "main_only")
    response = app.post("/api/branches/main/checkpoints", json={"assertion_id": a["id"]})
    expect_error(response, 422, "invalid_request")


def test_reconcile_and_retaint(app):
    p = uid("k") + ":"
    a = app.add(p + "a", 1, 100)
    z = app.add(p + "z", 1, 100)
    b = app.add(p + "b", 1, 200, parents=[a["id"], z["id"]])
    child = app.add(p + "c", 1, 300, parents=[b["id"]])
    assert app.retract(a["id"]).status_code == 200
    tainted_until = app.now
    t = app.t()
    response = app.post(
        f"/api/branches/main/assertions/{b['id']}/reconcile", json={"asserted_at": t}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active" and response.json()["tainted_by"] == []
    assert status(app, child["id"]) == ("tainted_pending_reconcile", [a["id"]])
    assert status(app, b["id"], asserted_at=tainted_until)[0] == "tainted_pending_reconcile"
    response = app.post(
        f"/api/branches/main/assertions/{b['id']}/reconcile", json={"asserted_at": app.t()}
    )
    expect_error(response, 409, "not_tainted")
    assert app.retract(z["id"]).status_code == 200
    assert status(app, b["id"]) == ("tainted_pending_reconcile", [z["id"]])
    assert status(app, child["id"]) == ("tainted_pending_reconcile", sorted([a["id"], z["id"]]))
    response = app.post(
        f"/api/branches/main/assertions/{uid('no-')}/reconcile", json={"asserted_at": app.t()}
    )
    expect_error(response, 404, "not_found")


def test_as_of_listings(app):
    p = uid("k") + ":"
    a = app.add(p + "a", 1, 100)
    b = app.add(p + "b", 1, 200, parents=[a["id"]])
    t_mid = app.now
    c = app.add(p + "c", 1, 300)
    assert app.retract(a["id"]).status_code == 200

    def ids(**params):
        params["prefix"] = p
        response = app.get("/api/branches/main/assertions", params=params)
        assert response.status_code == 200, response.text
        return [x["id"] for x in response.json()["assertions"]]

    assert ids() == [a["id"], b["id"], c["id"]]
    assert ids(asserted_at=t_mid) == [a["id"], b["id"]]
    assert ids(status="tainted_pending_reconcile") == [b["id"]]
    assert ids(status="retracted") == [a["id"]]
    assert ids(status="active") == [c["id"]]
    assert ids(status="active", asserted_at=t_mid) == [a["id"], b["id"]]
    expect_error(
        app.get(f"/api/branches/main/assertions/{c['id']}", params={"asserted_at": t_mid}),
        404,
        "not_found",
    )
