import hub as h
from conftest import calls, error_code


def test_new_protocol_handshake(app, hub):
    assert hub.step >= 20
    app.creds("docs", "tickets", "lingo")
    with hub.lock:
        hub.sessions.clear()
    response = app.get("/api/docs/search", params={"q": "deploy"})
    assert response.status_code == 200, response.text
    assert response.json() == hub.tool_docs("search_docs", {"query": "deploy", "limit": 5}, "a")
    inits = [
        c
        for c in calls(hub, "docs")
        if isinstance(c["body"], dict) and c["body"].get("method") == "initialize"
    ]
    assert inits and all(c["body"]["params"]["protocolVersion"] == h.MCP_NEW for c in inits)
    sessions = [
        c["headers"].get("mcp-session-id")
        for c in calls(hub, "docs")
        if isinstance(c["body"], dict) and c["body"].get("method") == "tools/call"
    ]
    assert sessions and all(sessions)


def test_features_still_work(app, hub):
    app.creds("docs", "tickets", "lingo")
    doc = app.get("/api/docs/doc-5").json()
    assert doc["title"] == h.DOCS[4][1]
    response = app.get("/api/docs/doc-404")
    assert response.status_code == 404 and error_code(response) == "not_found"
    ticket = app.post("/api/tickets", json={"title": "Upgrade", "body": "MCP"}).json()
    assert ticket["id"].startswith("T-")
    assert any(t["id"] == ticket["id"] for t in app.get("/api/tickets").json()["tickets"])
    output = app.post(
        "/api/capabilities/lingo.translate/invoke",
        json={"input": {"text": "thank you", "target": "es"}},
    ).json()["output"]
    assert output["translation"] == "gracias"
    rows = {r["name"]: r for r in app.get("/api/integrations").json()["integrations"]}
    assert rows["docs"]["status"] == rows["tickets"]["status"] == rows["lingo"]["status"] == "ok"


def test_expired_sessions_are_renewed(app, hub):
    app.creds("docs")
    assert app.get("/api/docs/search", params={"q": "flags"}).status_code == 200
    with hub.lock:
        hub.sessions.clear()
    response = app.get("/api/docs/search", params={"q": "flags"})
    assert response.status_code == 200 and response.json()["results"][0]["id"] == "doc-7"
