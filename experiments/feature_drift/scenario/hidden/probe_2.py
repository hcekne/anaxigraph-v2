from conftest import error_code, tool_calls


def invoke(app, name, payload, ws=None):
    return app.post(f"/api/capabilities/{name}/invoke", ws, json={"input": payload})


def test_notes(app, hub):
    ws = app.workspace()
    app.creds("notes", ws=ws)
    created = invoke(app, "notes.create", {"title": "Boiler", "text": "Check pressure"}, ws)
    assert created.status_code == 200, created.text
    note = created.json()["output"]
    assert note["id"] and note["title"] == "Boiler" and note["text"] == "Check pressure"
    found = invoke(app, "notes.search", {"q": "pressure"}, ws).json()["output"]
    assert found == {"notes": [note]}
    assert invoke(app, "notes.create", {"title": "x"}, ws).status_code == 422


def test_platform_rules(app, hub):
    ws = app.workspace()
    row = app.get("/api/integrations/notes", ws).json()
    assert row["kind"] == "mcp" and row["status"] == "unconfigured"
    app.creds("notes", ws=ws)
    assert app.get("/api/integrations/notes", ws).json()["status"] == "ok"
    app.settings(ws, quotas={"notes": 1})
    assert invoke(app, "notes.search", {"q": "a"}, ws).status_code == 200
    response = invoke(app, "notes.search", {"q": "a"}, ws)
    assert response.status_code == 429 and error_code(response) == "quota_exceeded"
    assert "notes" in {e["integration"] for e in app.audit(ws)}
    app.settings(ws, disabled_integrations=["notes"])
    response = invoke(app, "notes.search", {"q": "a"}, ws)
    assert response.status_code == 403 and error_code(response) == "integration_disabled"
    assert len(tool_calls(hub, "notes", "search_notes")) == 1
