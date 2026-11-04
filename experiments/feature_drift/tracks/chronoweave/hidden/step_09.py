"""Request 9: four pre-commit verifiers, configured per tenant."""

from conftest import expect_error

FOUR = ("burst_rate_cap", "escrow_hold", "gdpr_retention", "geofence_jurisdiction")
DAY = 86400


def configure(app, tenant, name, enabled=True, config=None):
    body = {"enabled": enabled}
    if config is not None:
        body["config"] = config
    response = app.put(f"/api/verifiers/{name}", tenant, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_listing_and_configuration(app):
    t = app.tenant()
    rows = app.get("/api/verifiers", t).json()["verifiers"]
    names = [r["name"] for r in rows]
    assert set(FOUR) <= set(names) and names == sorted(names)
    for row in rows:
        assert row["enabled"] is False and row["config"] is None
    config = {"holds": [{"key": "escrow:1", "until": 500}]}
    assert configure(app, t, "escrow_hold", config=config) == {
        "name": "escrow_hold",
        "enabled": True,
        "config": config,
    }
    listed = {r["name"]: r for r in app.get("/api/verifiers", t).json()["verifiers"]}
    assert listed["escrow_hold"]["config"] == config
    assert listed["gdpr_retention"] == {"name": "gdpr_retention", "enabled": False, "config": None}
    expect_error(app.put("/api/verifiers/nope", t, json={"enabled": False}), 404, "not_found")
    for name, bad in (
        ("escrow_hold", {"holds": "x"}),
        ("gdpr_retention", {"prefixes": ["pii:"], "max_age_days": 0}),
        ("geofence_jurisdiction", {"allowed": ["EU"]}),
        ("burst_rate_cap", {"max": 0, "window": 10}),
        ("burst_rate_cap", {"max": 2}),
    ):
        response = app.put(f"/api/verifiers/{name}", t, json={"enabled": True, "config": bad})
        expect_error(response, 422, "invalid_request")
    expect_error(
        app.put("/api/verifiers/gdpr_retention", t, json={"enabled": True}), 422, "invalid_request"
    )
    expect_error(
        app.put("/api/verifiers/gdpr_retention", t, json={"enabled": "yes", "config": {}}),
        422,
        "invalid_request",
    )
    off = configure(app, t, "escrow_hold", enabled=False)
    assert off["enabled"] is False
    assert app.write("escrow:1", 1, 1, tenant=t).status_code == 201
    other = app.tenant()  # settings are per tenant
    assert {r["name"]: r for r in app.get("/api/verifiers", other).json()["verifiers"]}[
        "escrow_hold"
    ]["config"] is None


def test_escrow_retention_geofence(app):
    t = app.tenant()
    configure(app, t, "escrow_hold", config={"holds": [{"key": "escrow:1", "until": 500}]})
    expect_error(app.write("escrow:1", 1, 499, tenant=t), 409, "escrow_hold")
    assert app.write("escrow:1", 1, 500, tenant=t).status_code == 201
    assert app.write("escrow:2", 1, 1, tenant=t).status_code == 201
    configure(app, t, "gdpr_retention", config={"prefixes": ["pii:", "hr:"], "max_age_days": 30})
    now = app.t(5_000_000)
    expect_error(
        app.write("pii:ann", 1, now - 30 * DAY - 1, tenant=t, asserted_at=now),
        409,
        "retention_expired",
    )
    assert app.write("pii:ann", 1, now - 30 * DAY, tenant=t, asserted_at=now).status_code == 201
    assert app.write("ops:x", 1, 0, tenant=t).status_code == 201
    configure(
        app,
        t,
        "geofence_jurisdiction",
        config={"allowed": ["EU", "NO"], "nodes": {"n-eu": "EU", "n-us": "US"}},
    )
    assert app.write("k", 1, 1, tenant=t, node="n-eu").status_code == 201
    expect_error(app.write("k", 1, 1, tenant=t, node="n-us"), 409, "jurisdiction_denied")
    expect_error(app.write("k", 1, 1, tenant=t, node="unknown"), 409, "jurisdiction_denied")
    # branch writes and retractions are not verified
    name = app.fork(tenant=t)
    assert app.write("k", 1, 1, tenant=t, node="n-us", branch=name).status_code == 201
    held = app.state(tenant=t)["escrow:1"][1]
    assert app.retract(held, tenant=t).status_code == 200
    # ...but a merge is
    expect_error(app.merge(name, tenant=t), 409, "jurisdiction_denied")


def test_burst_rate_cap_and_atomic_merges(app):
    t = app.tenant()
    configure(app, t, "burst_rate_cap", config={"max": 2, "window": 100})
    start = app.t(1000)
    app.add("a", 1, 1, tenant=t, author="alice", asserted_at=start)
    gone = app.add("b", 1, 1, tenant=t, author="alice", asserted_at=start + 50)
    assert app.retract(gone["id"], tenant=t, asserted_at=start + 60).status_code == 200
    expect_error(
        app.write("c", 1, 1, tenant=t, author="alice", asserted_at=start + 99),
        409,
        "rate_cap_exceeded",
    )
    assert app.write("c", 1, 1, tenant=t, author="bob", asserted_at=start + 99).status_code == 201
    assert (
        app.write("c", 1, 1, tenant=t, author="alice", asserted_at=start + 100).status_code == 201
    )
    app.now = start + 1000
    name = app.fork(tenant=t)
    for key in ("x", "y", "z"):
        app.add(key, 1, 1, branch=name, tenant=t, author="carol")
    before = app.state(tenant=t)
    expect_error(app.merge(name, tenant=t), 409, "rate_cap_exceeded")
    assert app.state(tenant=t) == before
    row = next(b for b in app.get("/api/branches", t).json()["branches"] if b["name"] == name)
    assert row["status"] == "active"


def test_name_order_and_independence(app):
    t = app.tenant()
    configure(app, t, "geofence_jurisdiction", config={"allowed": ["EU"], "nodes": {}})
    configure(app, t, "escrow_hold", config={"holds": [{"key": "k", "until": 10}]})
    expect_error(app.write("k", 1, 1, tenant=t), 409, "escrow_hold")  # escrow_hold < geofence...
    configure(app, t, "burst_rate_cap", config={"max": 1, "window": 1})
    rows = {r["name"]: r for r in app.get("/api/verifiers", t).json()["verifiers"]}
    assert rows["escrow_hold"]["config"] == {"holds": [{"key": "k", "until": 10}]}
    assert rows["geofence_jurisdiction"]["config"] == {"allowed": ["EU"], "nodes": {}}
    app.restart()
    rows = {r["name"]: r for r in app.get("/api/verifiers", t).json()["verifiers"]}
    assert rows["burst_rate_cap"] == {
        "name": "burst_rate_cap",
        "enabled": True,
        "config": {"max": 1, "window": 1},
    }
    expect_error(app.write("k", 1, 1, tenant=t), 409, "escrow_hold")
    expect_error(app.write("other", 1, 1, tenant=t), 409, "jurisdiction_denied")
