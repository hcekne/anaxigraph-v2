"""Request 14 (fault probe P2): poisoned promotions are quarantined atomically."""

import time

from conftest import expect_error, uid


def branch_status(app, tenant, name):
    return next(
        b for b in app.get("/api/branches", tenant).json()["branches"] if b["name"] == name
    )["status"]


def poisoned(app, t):
    """main: a1 (later retracted), keep. Branch: free (earliest, unpoisoned), h1 <- a1, h2 <- h1."""
    a1 = app.add("base", 1, 100, tenant=t)
    keep = app.add("keep", 1, 100, tenant=t)
    name = app.fork(tenant=t)
    free = app.add("free", 1, 5, branch=name, tenant=t, parents=[keep["id"]])
    h1 = app.add("h1", 1, 200, branch=name, tenant=t, parents=[a1["id"]])
    h2 = app.add("h2", 1, 300, branch=name, tenant=t, parents=[h1["id"]])
    assert app.retract(a1["id"], tenant=t).status_code == 200  # concurrent with the branch
    return name, a1, free, h1, h2


def test_poisoned_promotion_is_quarantined(app):
    t = app.tenant()
    name, a1, free, h1, h2 = poisoned(app, t)
    before = (app.state(tenant=t), app.get("/api/branches/main/assertions", t).json())
    at = app.t()
    response = app.merge(name, tenant=t, asserted_at=at)
    expect_error(response, 409, "promotion_quarantined")
    receipts = app.get("/api/quarantine", t).json()["receipts"]
    assert receipts == [
        {
            "id": "qr-1",
            "branch": name,
            "asserted_at": at,
            "reason": "parent_retracted",
            "assertions": [h1["id"], h2["id"]],
            "retracted_parents": [a1["id"]],
        }
    ]
    assert "qr-1" in response.json()["error"]["message"]
    assert app.get("/api/quarantine/qr-1", t).json() == receipts[0]
    expect_error(app.get("/api/quarantine/qr-99", t), 404, "not_found")
    # main is untouched: not even the unpoisoned assertion was promoted
    assert (app.state(tenant=t), app.get("/api/branches/main/assertions", t).json()) == before
    expect_error(app.get(f"/api/branches/main/assertions/{free['id']}", t), 404, "not_found")
    assert branch_status(app, t, name) == "quarantined"
    expect_error(app.write("x", 1, 1, branch=name, tenant=t), 409, "branch_closed")
    expect_error(app.merge(name, tenant=t), 409, "branch_closed")
    assert app.state(name, tenant=t)["h2"][1] == h2["id"]  # still readable
    app.restart()
    assert app.get("/api/quarantine", t).json()["receipts"] == receipts


def test_quarantine_leaves_no_trace_on_main(app, hub):
    t = app.tenant(max_active_branches=5)
    rule = {
        "id": "r",
        "capability": "c",
        "requires": [{"key": "keep", "equals": 1}],
        "unless": [{"key": "h1", "equals": 1}],
    }
    assert app.post("/api/rules", t, json=rule).status_code == 201
    sink = uid("s")
    app.post("/api/sinks", t, json={"id": sink, "url": f"{hub.url}/sinks/{sink}", "secret": "s"})
    name, *_ = poisoned(app, t)
    clock = app.now
    active = app.get(f"/api/tenants/{t}").json()["usage"]["active_branches"]
    expect_error(app.merge(name, tenant=t), 409, "promotion_quarantined")
    assert app.get("/api/obligations", t).json()["obligations"] == []
    assert (
        app.write("after", 1, 1, tenant=t, asserted_at=clock).status_code == 201
    )  # clock unchanged
    assert app.get(f"/api/tenants/{t}").json()["usage"]["active_branches"] == active - 1
    time.sleep(0.5)
    assert hub.calls_to("sinks", f"/sinks/{sink}") == []
    second, *_ = poisoned(app, t)
    expect_error(app.merge(second, tenant=t), 409, "promotion_quarantined")
    assert [r["id"] for r in app.get("/api/quarantine", t).json()["receipts"]] == ["qr-1", "qr-2"]


def test_insulated_or_old_taint_is_not_poison(app):
    t = app.tenant()
    # a checkpoint recorded before the retraction insulates the branch's work
    a1 = app.add("a1", 1, 1, tenant=t)
    c = app.add("c", 1, 2, tenant=t, parents=[a1["id"]])
    app.post(
        "/api/branches/main/checkpoints", t, json={"assertion_id": c["id"], "asserted_at": app.t()}
    )
    name = app.fork(tenant=t)
    h = app.add("h", 1, 3, branch=name, tenant=t, parents=[c["id"]])
    assert app.retract(a1["id"], tenant=t).status_code == 200
    response = app.merge(name, tenant=t)
    assert response.status_code == 200, response.text
    assert app.record(h["id"], tenant=t)["status"] == "active"
    # taint from a retraction that happened before the fork is not concurrent: promote
    old = app.add("old", 1, 1, tenant=t)
    mid = app.add("mid", 1, 2, tenant=t, parents=[old["id"]])
    assert app.retract(old["id"], tenant=t).status_code == 200
    name = app.fork(tenant=t)
    built = app.add("built", 1, 3, branch=name, tenant=t, parents=[mid["id"]])
    response = app.merge(name, tenant=t)
    assert response.status_code == 200, response.text
    assert app.record(built["id"], tenant=t)["tainted_by"] == [old["id"]]
    assert app.get("/api/quarantine", t).json()["receipts"] == []


def test_resolution_happens_before_the_poison_check(app):
    t = app.tenant()
    a1 = app.add("a1", 1, 1, tenant=t)
    name = app.fork(tenant=t)
    bad = app.add("k", "branch", 10, branch=name, tenant=t, parents=[a1["id"]])
    fine = app.add("other", 1, 10, branch=name, tenant=t)
    app.add("k", "main", 20, tenant=t)  # main asserted later: main wins the conflict on k
    assert app.retract(a1["id"], tenant=t).status_code == 200
    response = app.merge(name, tenant=t, resolver="last_assertion_wins")
    assert response.status_code == 200, response.text
    assert response.json()["discarded"] == [bad["id"]] and response.json()["promoted"] == [
        fine["id"]
    ]
