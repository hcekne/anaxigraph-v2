import hub as h
from conftest import close, error_code

REQUIRED = {
    "weatherco.forecast": ({"city": "string", "days": "integer"}, {"city"}),
    "crm.search_contacts": ({"search": "string", "limit": "integer"}, set()),
    "crm.create_contact": (
        {"name": "string", "email": "string", "company": "string"},
        {"name", "email"},
    ),
    "docs.search": ({"q": "string", "limit": "integer"}, {"q"}),
    "docs.get": ({"id": "string"}, {"id"}),
    "calendar.list_events": ({"from": "string", "to": "string"}, {"from", "to"}),
    "ledger.list_invoices": ({"status": "string"}, set()),
    "tickets.create": (
        {"title": "string", "body": "string", "priority": "string"},
        {"title", "body"},
    ),
    "tickets.list": ({"status": "string"}, set()),
}


def catalogue(app, ws=None):
    response = app.get("/api/capabilities", ws)
    assert response.status_code == 200, response.text
    return response.json()["capabilities"]


def invoke(app, name, payload, ws=None):
    return app.post(f"/api/capabilities/{name}/invoke", ws, json={"input": payload})


def test_catalogue_schemas(app, hub):
    rows = catalogue(app)
    assert [r["name"] for r in rows] == sorted(r["name"] for r in rows)
    by_name = {r["name"]: r for r in rows}
    for name, (properties, required) in REQUIRED.items():
        row = by_name[name]
        assert row["integration"] == name.split(".")[0]
        assert isinstance(row["description"], str) and row["description"]
        schema = row["input_schema"]
        assert schema["type"] == "object"
        for field, kind in properties.items():
            assert schema["properties"][field]["type"] == kind, (name, field)
        assert set(schema.get("required", [])) == required, name


def test_availability_follows_credentials(fresh_app, hub):
    rows = {r["name"]: r for r in catalogue(fresh_app)}
    assert not any(rows[name]["available"] for name in REQUIRED)
    fresh_app.creds("crm")
    rows = {r["name"]: r for r in catalogue(fresh_app)}
    assert rows["crm.search_contacts"]["available"] and rows["crm.create_contact"]["available"]
    assert not rows["docs.search"]["available"]
    response = invoke(fresh_app, "docs.search", {"q": "deploy"})
    assert response.status_code == 409 and error_code(response) == "not_configured"


def test_invoke_matches_endpoints(app, hub):
    app.creds("weatherco", "crm", "docs", "calendar", "ledger", "tickets")
    pairs = [
        (
            "weatherco.forecast",
            {"city": "Oslo", "days": 2},
            "/api/weather",
            {"city": "Oslo", "days": 2},
        ),
        ("crm.search_contacts", {"search": "ann"}, "/api/contacts", {"search": "ann"}),
        (
            "docs.search",
            {"q": "deploy", "limit": 2},
            "/api/docs/search",
            {"q": "deploy", "limit": 2},
        ),
        ("docs.get", {"id": "doc-4"}, "/api/docs/doc-4", {}),
        (
            "calendar.list_events",
            {"from": "2026-01-10", "to": "2026-01-14"},
            "/api/calendar/events",
            {"from": "2026-01-10", "to": "2026-01-14"},
        ),
        ("ledger.list_invoices", {"status": "paid"}, "/api/invoices", {"status": "paid"}),
    ]
    for name, payload, path, params in pairs:
        response = invoke(app, name, payload)
        assert response.status_code == 200, (name, response.text)
        assert close(response.json()["output"], app.get(path, params=params).json()), name


def test_invoke_writes(app, hub):
    app.creds("crm", "tickets")
    response = invoke(app, "tickets.create", {"title": "Via assistant", "body": "Hello"})
    assert response.status_code == 200, response.text
    ticket = response.json()["output"]
    assert ticket["id"].startswith("T-") and ticket["status"] == "open"
    listed = invoke(app, "tickets.list", {}).json()["output"]["tickets"]
    assert any(t["id"] == ticket["id"] for t in listed)
    response = invoke(app, "crm.create_contact", {"name": "Ivy Assist", "email": "ivy@assist.test"})
    assert response.status_code == 200 and response.json()["output"]["email"] == "ivy@assist.test"


def test_invoke_errors(app, hub):
    app.creds("weatherco", "docs")
    response = invoke(app, "nothing.here", {})
    assert response.status_code == 404 and error_code(response) == "not_found"
    for payload in ({}, {"city": 5}, {"city": "Oslo", "days": "x"}):
        response = invoke(app, "weatherco.forecast", payload)
        assert response.status_code == 422 and error_code(response) == "invalid_request", payload
    response = invoke(app, "weatherco.forecast", {"city": "Atlantis"})
    assert response.status_code == 404 and error_code(response) == "unknown_city"
    response = invoke(app, "docs.get", {"id": "doc-404"})
    assert response.status_code == 404 and error_code(response) == "not_found"
    response = app.post("/api/capabilities/docs.get/invoke", json={})
    assert response.status_code == 422


def test_forecast_output(app, hub):
    app.creds("weatherco")
    output = invoke(app, "weatherco.forecast", {"city": "Geneva"}).json()["output"]
    assert close(output, {"city": "Geneva", "country": "CH", "days": h.forecast("geneva", 3)})
