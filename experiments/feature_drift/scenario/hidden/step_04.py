from conftest import calls, error_code, fault

FIRST = {"crm": "rest", "docs": "mcp", "weatherco": "rest"}


def entries(app):
    response = app.get("/api/integrations")
    assert response.status_code == 200, response.text
    return response.json()["integrations"]


def test_unconfigured_overview_makes_no_calls(fresh_app, hub):
    rows = entries(fresh_app)
    assert [r["name"] for r in rows] == sorted(r["name"] for r in rows)
    by_name = {r["name"]: r for r in rows}
    assert set(FIRST) <= set(by_name)
    for name, kind in FIRST.items():
        assert by_name[name]["kind"] == kind
        assert by_name[name]["status"] == "unconfigured"
    assert hub.calls == []


def test_configured_overview_checks_live(app, hub):
    app.creds(*FIRST)
    by_name = {r["name"]: r for r in entries(app)}
    for name in FIRST:
        assert by_name[name]["status"] == "ok", by_name[name]
        assert by_name[name]["detail"] is None
        assert calls(hub, name), f"no live check for {name}"


def test_failing_provider_shows_error(app, hub):
    app.creds(*FIRST)
    fault(hub, "weatherco", fail_next=50, fail_status=503)
    row = app.get("/api/integrations/weatherco").json()
    assert row["name"] == "weatherco" and row["kind"] == "rest"
    assert row["status"] == "error" and isinstance(row["detail"], str) and row["detail"]
    assert app.get("/api/integrations/crm").json()["status"] == "ok"


def test_rejected_credentials_show_error(app, hub):
    app.put("/api/credentials/docs", json={"token": "bad"})
    assert app.get("/api/integrations/docs").json()["status"] == "error"
    app.creds("docs")


def test_unknown_integration(app, hub):
    response = app.get("/api/integrations/nope")
    assert response.status_code == 404 and error_code(response) == "not_found"
