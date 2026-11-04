"""Request 8: tenant isolation and per-tenant budgets."""

import hashlib
import hmac

from conftest import expect_error, uid


def tenant_row(app, ident):
    response = app.get(f"/api/tenants/{ident}")
    assert response.status_code == 200, response.text
    return response.json()


def test_tenant_lifecycle(app):
    ident = uid("t-")
    response = app.post("/api/tenants", json={"id": ident, "budgets": {"max_active_branches": 2}})
    assert response.status_code == 201, response.text
    assert response.json() == {
        "id": ident,
        "budgets": {"max_active_branches": 2, "max_assertion_bytes": None},
        "usage": {"active_branches": 0, "assertion_bytes": 0},
    }
    plain = uid("t-")
    assert app.post("/api/tenants", json={"id": plain}).json()["budgets"] == {
        "max_active_branches": None,
        "max_assertion_bytes": None,
    }
    listed = app.get("/api/tenants").json()["tenants"]
    ids = [t["id"] for t in listed]
    assert ids == sorted(ids) and {"default", ident, plain} <= set(ids)
    default = next(t for t in listed if t["id"] == "default")
    assert default["budgets"] == {"max_active_branches": None, "max_assertion_bytes": None}
    expect_error(app.post("/api/tenants", json={"id": ident}), 409, "conflict")
    expect_error(app.post("/api/tenants", json={"id": "default"}), 409, "conflict")
    for bad in (
        {"id": "A"},
        {"id": "x"},
        {"id": "x" * 33},
        {"id": "no_underscore"},
        {"id": uid("t-"), "budgets": {"max_active_branches": -1}},
        {"id": uid("t-"), "budgets": {"max_assertion_bytes": "10"}},
    ):
        expect_error(app.post("/api/tenants", json=bad), 422, "invalid_request")
    response = app.put(
        f"/api/tenants/{ident}/budgets",
        json={"max_active_branches": None, "max_assertion_bytes": 500},
    )
    assert response.status_code == 200, response.text
    assert response.json()["budgets"] == {"max_active_branches": None, "max_assertion_bytes": 500}
    assert tenant_row(app, ident)["budgets"]["max_assertion_bytes"] == 500
    expect_error(app.get(f"/api/tenants/{uid('t-')}"), 404, "not_found")


def test_unknown_tenant_header(app):
    ghost = uid("t-")
    for method, path in (
        ("GET", "/api/branches/main/state"),
        ("GET", "/api/branches"),
        ("GET", "/api/branches/main/assertions"),
        ("GET", "/api/branches/main/seals"),
        ("GET", "/api/rules"),
        ("GET", "/api/obligations"),
        ("GET", "/api/resolvers"),
    ):
        expect_error(app.call(method, path, ghost), 404, "unknown_tenant")
    expect_error(app.write("k", 1, 1, tenant=ghost), 404, "unknown_tenant")
    assert app.get("/api/health", ghost).status_code == 200
    assert app.get("/api/tenants", ghost).status_code == 200


def test_everything_is_isolated(app):
    a, b = app.tenant(), app.tenant()
    shared = uid("same-")
    ra = app.add("k", "A", 10, id=shared, tenant=a)
    t_a = ra["asserted_at"]
    rb = app.add("k", "B", 20, id=shared, tenant=b, asserted_at=t_a - 500)  # B has its own clock
    assert app.state(tenant=a) == {"k": ("A", shared)}
    assert app.state(tenant=b) == {"k": ("B", shared)}
    assert rb["asserted_at"] == t_a - 500
    # branches with the same name
    name = uid("b-")
    app.fork(name, tenant=a)
    app.fork(name, tenant=b)
    app.add("h", 1, 1, branch=name, tenant=a)
    assert "h" not in app.state(name, tenant=b)
    # seals and the authority secret
    app.seal(0, 100, ident="q", tenant=a)
    expect_error(app.write("k", 2, 50, tenant=a), 409, "sealed_interval")
    assert app.write("k", 2, 50, tenant=b).status_code == 201
    assert app.put("/api/authority", a, json={"secret": "secret-a"}).status_code == 204
    app.seal(0, 100, ident="q", tenant=b)
    ident = uid("o-")
    proof = hmac.new(b"secret-a", f"assert:q:{ident}".encode(), hashlib.sha256).hexdigest()
    override = {"seal_id": "q", "proof": proof}
    expect_error(
        app.write("k", 3, 50, id=ident, tenant=b, override=override), 403, "invalid_override"
    )
    assert app.write("k", 3, 50, id=ident, tenant=a, override=override).status_code == 201
    # rules and obligations
    rule = {"id": "r", "capability": "c", "requires": [{"key": "role", "equals": 1}], "unless": []}
    assert app.post("/api/rules", a, json=rule).status_code == 201
    assert app.get("/api/rules", b).json()["rules"] == []
    assert app.post("/api/rules", b, json=rule).status_code == 201
    app.add("role", 1, 201, tenant=a)
    app.add("role", 2, 205, tenant=a)
    assert len(app.get("/api/obligations", a).json()["obligations"]) == 1
    assert app.get("/api/obligations", b).json()["obligations"] == []


def test_branch_budget(app):
    ident = app.tenant(max_active_branches=2)
    one, two = app.fork(tenant=ident), app.fork(tenant=ident)
    assert tenant_row(app, ident)["usage"]["active_branches"] == 2
    response = app.post("/api/branches", ident, json={"name": uid("b-")})
    expect_error(response, 409, "budget_exceeded")
    response = app.delete(f"/api/branches/{one}", ident)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "discarded"
    expect_error(app.write("k", 1, 1, branch=one, tenant=ident), 409, "branch_closed")
    expect_error(app.merge(one, tenant=ident), 409, "branch_closed")
    expect_error(app.delete(f"/api/branches/{one}", ident), 409, "branch_closed")
    expect_error(app.delete("/api/branches/main", ident), 409, "main_only")
    three = app.fork(tenant=ident)
    app.add("k", 1, 1, branch=two, tenant=ident)
    assert app.merge(two, tenant=ident).status_code == 200
    app.fork(tenant=ident)
    assert tenant_row(app, ident)["usage"]["active_branches"] == 2
    rows = {b["name"]: b["status"] for b in app.get("/api/branches", ident).json()["branches"]}
    assert rows[one] == "discarded" and rows[two] == "merged" and rows[three] == "active"


def test_byte_budget(app):
    ident = app.tenant(max_assertion_bytes=20)
    app.add("a", "abc", 1, tenant=ident)  # "abc" is 5 bytes
    app.add("b", {"n": 1}, 1, tenant=ident)  # {"n":1} is 7 bytes
    assert tenant_row(app, ident)["usage"]["assertion_bytes"] == 12
    name = app.fork(tenant=ident)
    app.add("c", [1, 2], 1, branch=name, tenant=ident)  # 5 bytes -> 17
    expect_error(app.write("d", "abcd", 1, tenant=ident), 409, "budget_exceeded")  # 6 bytes
    assert "d" not in app.state(tenant=ident)
    assert app.write("d", "a", 1, tenant=ident).status_code == 201  # 3 bytes -> exactly 20
    usage = tenant_row(app, ident)["usage"]["assertion_bytes"]
    assert usage == 20
    assert app.merge(name, tenant=ident).status_code == 200  # moving bytes is not growth
    assert tenant_row(app, ident)["usage"]["assertion_bytes"] == usage
    other = app.fork(tenant=ident)
    expect_error(app.write("e", 1, 1, branch=other, tenant=ident), 409, "budget_exceeded")
    app.put(
        f"/api/tenants/{ident}/budgets",
        json={"max_active_branches": None, "max_assertion_bytes": 10},
    )
    expect_error(app.write("f", 1, 1, tenant=ident), 409, "budget_exceeded")
    assert app.retract(app.state(tenant=ident)["a"][1], tenant=ident).status_code == 200


def test_tenants_survive_restart(fresh_app):
    app = fresh_app
    ident = app.tenant(max_active_branches=3, max_assertion_bytes=1000)
    record = app.add("k", "v", 1, tenant=ident)
    name = app.fork(tenant=ident)
    app.restart()
    row = tenant_row(app, ident)
    assert row["budgets"] == {"max_active_branches": 3, "max_assertion_bytes": 1000}
    assert row["usage"] == {"active_branches": 1, "assertion_bytes": 3}
    assert app.state(tenant=ident) == {"k": ("v", record["id"])}
    assert name in [b["name"] for b in app.get("/api/branches", ident).json()["branches"]]
    assert app.state() == {}
