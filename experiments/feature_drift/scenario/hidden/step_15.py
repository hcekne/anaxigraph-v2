from conftest import calls, error_code


def test_disabled_integration(app, hub):
    ws = app.workspace()
    app.creds("crm", "weatherco", ws=ws)
    app.settings(ws, disabled_integrations=["crm"], quotas={"crm": 10})
    row = app.get("/api/integrations/crm", ws).json()
    assert row["status"] == "disabled"
    caps = {r["name"]: r for r in app.get("/api/capabilities", ws).json()["capabilities"]}
    assert not caps["crm.search_contacts"]["available"] and caps["weatherco.forecast"]["available"]
    response = app.get("/api/contacts", ws)
    assert response.status_code == 403 and error_code(response) == "integration_disabled"
    response = app.post(
        "/api/capabilities/crm.create_contact/invoke",
        ws,
        json={"input": {"name": "X", "email": "x@y.test"}},
    )
    assert response.status_code == 403 and error_code(response) == "integration_disabled"
    assert calls(hub, "crm") == []
    assert app.get("/api/usage", ws).json()["integrations"]["crm"]["used"] == 0
    assert app.get("/api/weather", ws, params={"city": "Oslo"}).status_code == 200
    app.settings(ws, disabled_integrations=[])
    assert app.get("/api/contacts", ws).status_code == 200
    assert app.get("/api/integrations/crm", ws).json()["status"] == "ok"


def test_disabled_is_per_workspace(app, hub):
    ws = app.workspace()
    app.creds("crm")
    app.settings(ws, disabled_integrations=["crm"])
    assert app.get("/api/contacts").status_code == 200


def test_workflow_step_fails_when_disabled(app, hub):
    ws = app.workspace()
    app.creds("crm", ws=ws)
    workflow = app.post(
        "/api/workflows",
        ws,
        json={
            "name": "Lookup",
            "steps": [{"id": "find", "capability": "crm.search_contacts", "input": {}}],
        },
    ).json()
    app.settings(ws, disabled_integrations=["crm"])
    run = app.post(f"/api/workflows/{workflow['id']}/run", ws, json={"params": {}}).json()
    assert run["status"] == "failed"
    assert run["steps"][0]["error"]["code"] == "integration_disabled"


def test_unknown_names_are_rejected(app, hub):
    ws = app.workspace()
    response = app.put(f"/api/workspaces/{ws}/settings", json={"disabled_integrations": ["fax"]})
    assert response.status_code == 422 and error_code(response) == "invalid_request"


def test_credential_removal(app, hub):
    ws = app.workspace()
    app.creds("weatherco", ws=ws)
    assert app.delete("/api/credentials/weatherco", ws).status_code == 204
    assert app.get("/api/integrations/weatherco", ws).json()["status"] == "unconfigured"
    response = app.get("/api/weather", ws, params={"city": "Oslo"})
    assert response.status_code == 409 and error_code(response) == "not_configured"
    response = app.delete("/api/credentials/fax", ws)
    assert response.status_code == 404 and error_code(response) == "not_found"
