from datetime import datetime, timezone

from conftest import calls, error_code


def usage(app, ws):
    response = app.get("/api/usage", ws)
    assert response.status_code == 200, response.text
    return response.json()


def test_quota_blocks_without_calling_provider(app, hub):
    ws = app.workspace()
    app.creds("crm", ws=ws)
    app.settings(ws, quotas={"crm": 2})
    assert app.get("/api/contacts", ws, params={"search": "ann"}).status_code == 200
    assert app.get("/api/contacts", ws).status_code == 200
    sent = len(calls(hub, "crm"))
    response = app.get("/api/contacts", ws)
    assert response.status_code == 429 and error_code(response) == "quota_exceeded"
    assert len(calls(hub, "crm")) == sent
    report = usage(app, ws)
    assert report["integrations"]["crm"] == {"used": 2, "quota": 2}
    assert report["period"] == datetime.now(timezone.utc).strftime("%Y-%m")


def test_every_integration_reported(app, hub):
    ws = app.workspace()
    report = usage(app, ws)["integrations"]
    expected = {"weatherco", "crm", "docs", "calendar", "ledger", "tickets", "skycast"}
    assert expected <= set(report)
    assert all(report[name] == {"used": 0, "quota": None} for name in expected)


def test_billing_units(app, hub):
    ws = app.workspace()
    app.creds("crm", "ledger", "weatherco", ws=ws)
    app.settings(ws, quotas={"ledger": 3})
    app.get("/api/contacts", ws)  # three pages, one operation
    assert usage(app, ws)["integrations"]["crm"]["used"] == 1
    assert app.get("/api/invoices", ws).status_code == 200
    assert usage(app, ws)["integrations"]["ledger"] == {"used": 2, "quota": 3}
    response = app.get("/api/invoices", ws)
    assert response.status_code == 429 and error_code(response) == "quota_exceeded"
    app.get("/api/weather", ws, params={"city": "Oslo", "days": 99})
    app.get("/api/integrations", ws)
    assert usage(app, ws)["integrations"]["weatherco"]["used"] == 0
    app.post("/api/capabilities/weatherco.forecast/invoke", ws, json={"input": {"city": "Oslo"}})
    assert usage(app, ws)["integrations"]["weatherco"]["used"] == 1


def test_provider_failures_still_count(app, hub):
    ws = app.workspace()
    app.put("/api/credentials/crm", ws, json={"token": "wrong"})
    assert app.get("/api/contacts", ws).status_code == 502
    assert usage(app, ws)["integrations"]["crm"]["used"] == 1


def test_validation_and_persistence(app, hub):
    ws = app.workspace()
    for quotas in ({"crm": -1}, {"crm": "lots"}, {"unknown-integration": 5}):
        response = app.put(f"/api/workspaces/{ws}/settings", json={"quotas": quotas})
        assert response.status_code == 422 and error_code(response) == "invalid_request", quotas
    app.creds("crm", ws=ws)
    app.settings(ws, quotas={"crm": 5})
    app.get("/api/contacts", ws)
    app.restart()
    assert usage(app, ws)["integrations"]["crm"] == {"used": 1, "quota": 5}
    assert app.get(f"/api/workspaces/{ws}/settings").json()["quotas"] == {"crm": 5}
