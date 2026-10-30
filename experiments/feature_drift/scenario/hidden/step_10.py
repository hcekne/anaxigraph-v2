import hub as h
from conftest import calls, error_code


def test_workspace_lifecycle(app, hub):
    ident = app.workspace()
    listed = app.get("/api/workspaces").json()["workspaces"]
    assert {"id": "default", "name": "Default"} in listed
    assert {"id": ident, "name": ident.upper()} in listed
    assert app.get(f"/api/workspaces/{ident}").json() == {"id": ident, "name": ident.upper()}
    response = app.post("/api/workspaces", json={"id": ident, "name": "again"})
    assert response.status_code == 409 and error_code(response) == "conflict"
    for bad in ("No!", "a", "x" * 33, "UPPER"):
        response = app.post("/api/workspaces", json={"id": bad, "name": "Bad"})
        assert response.status_code == 422 and error_code(response) == "invalid_request", bad
    response = app.get("/api/workspaces/missing-ws")
    assert response.status_code == 404 and error_code(response) == "not_found"


def test_unknown_workspace_header(app, hub):
    for path in ("/api/weather?city=Oslo", "/api/integrations", "/api/audit", "/api/capabilities"):
        response = app.get(path, "does-not-exist")
        assert response.status_code == 404 and error_code(response) == "unknown_workspace", path
    assert app.get("/api/health", "does-not-exist").status_code == 200


def test_credentials_are_isolated(app, hub):
    app.creds("crm")
    north = app.workspace()
    response = app.get("/api/contacts", north)
    assert response.status_code == 409 and error_code(response) == "not_configured"
    app.creds("crm", ws=north, variant="b")
    body = app.get("/api/contacts", north).json()
    assert body["total"] == 8
    assert {c["company"] for c in body["contacts"]} == {"Northwind Traders"}
    assert app.get("/api/contacts").json()["total"] == 25
    assert app.get("/api/contacts", "default").json()["total"] == 25
    tokens = [c["headers"]["authorization"] for c in calls(hub, "crm")]
    assert "Token crm-token-b" in tokens and "Token crm-token-a" in tokens


def test_status_capabilities_and_tickets_are_per_workspace(app, hub):
    app.creds("tickets", "docs")
    other = app.workspace()
    rows = {r["name"]: r for r in app.get("/api/integrations", other).json()["integrations"]}
    assert rows["docs"]["status"] == "unconfigured"
    caps = {r["name"]: r for r in app.get("/api/capabilities", other).json()["capabilities"]}
    assert not caps["docs.search"]["available"]
    app.creds("tickets", ws=other, variant="b")
    app.post("/api/tickets", other, json={"title": "Northwind only", "body": "x"})
    mine = app.get("/api/tickets").json()["tickets"]
    theirs = app.get("/api/tickets", other).json()["tickets"]
    assert [t["title"] for t in theirs] == ["Northwind only"]
    assert all(t["title"] != "Northwind only" for t in mine)


def test_audit_is_isolated(app, hub):
    app.creds("weatherco")
    other = app.workspace()
    app.creds("weatherco", ws=other)
    default_before = {e["id"] for e in app.audit()}
    app.get("/api/weather", other, params={"city": "Oslo"})
    theirs = app.audit(other)
    assert theirs and {e["integration"] for e in theirs} == {"weatherco"}
    assert {e["id"] for e in app.audit()} == default_before


def test_everything_survives_restart(app, hub):
    ident = app.workspace()
    app.creds("weatherco", "crm", ws=ident)
    assert app.get("/api/weather", ident, params={"city": "Oslo"}).status_code == 200
    audit = {e["id"] for e in app.audit(ident)}
    app.restart()
    assert app.get(f"/api/workspaces/{ident}").status_code == 200
    response = app.get("/api/weather", ident, params={"city": "Denver"})
    assert (
        response.status_code == 200
        and response.json()["days"][0]["date"] == h.forecast("denver", 1)[0]["date"]
    )
    assert audit <= {e["id"] for e in app.audit(ident)}
    assert app.get("/api/contacts", ident).json()["total"] == 25
