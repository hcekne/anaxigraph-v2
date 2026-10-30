import time

from conftest import calls, tool_calls


def forecasts(hub, service="weatherco"):
    return [c for c in calls(hub, service) if "forecast" in c["path"]]


def test_cache_hits_skip_provider_quota_and_audit(app, hub):
    ws = app.workspace()
    app.creds("weatherco", ws=ws)
    app.settings(ws, cache_ttl_seconds={"weatherco": 60}, quotas={"weatherco": 10})
    first = app.get("/api/weather", ws, params={"city": "Oslo"})
    audit = len(app.audit(ws))
    second = app.get("/api/weather", ws, params={"city": "Oslo"})
    assert first.status_code == second.status_code == 200
    assert first.headers["x-cache"] == "miss" and second.headers["x-cache"] == "hit"
    assert first.json() == second.json()
    assert len(forecasts(hub)) == 1
    assert len(app.audit(ws)) == audit
    assert app.get("/api/usage", ws).json()["integrations"]["weatherco"]["used"] == 1
    third = app.get("/api/weather", ws, params={"city": "Oslo", "days": 4})
    assert third.headers["x-cache"] == "miss" and len(forecasts(hub)) == 2


def test_no_caching_by_default(app, hub):
    ws = app.workspace()
    app.creds("weatherco", ws=ws)
    app.get("/api/weather", ws, params={"city": "Oslo"})
    app.get("/api/weather", ws, params={"city": "Oslo"})
    assert len(forecasts(hub)) == 2


def test_cache_is_per_workspace_and_clearable(app, hub):
    first, second = app.workspace(), app.workspace()
    for ws in (first, second):
        app.creds("docs", ws=ws)
        app.settings(ws, cache_ttl_seconds={"docs": 60})
    app.get("/api/docs/search", first, params={"q": "deploy"})
    assert app.get("/api/docs/search", second, params={"q": "deploy"}).headers["x-cache"] == "miss"
    assert app.get("/api/docs/search", first, params={"q": "deploy"}).headers["x-cache"] == "hit"
    assert app.delete("/api/cache", first).status_code == 204
    assert app.get("/api/docs/search", first, params={"q": "deploy"}).headers["x-cache"] == "miss"
    assert len(tool_calls(hub, "docs", "search_docs")) == 3


def test_expiry_and_credential_change(app, hub):
    ws = app.workspace()
    app.creds("crm", ws=ws)
    app.settings(ws, cache_ttl_seconds={"crm": 1})
    app.get("/api/contacts", ws, params={"search": "ann"})
    assert app.get("/api/contacts", ws, params={"search": "ann"}).headers["x-cache"] == "hit"
    time.sleep(1.3)
    assert app.get("/api/contacts", ws, params={"search": "ann"}).headers["x-cache"] == "miss"
    app.settings(ws, cache_ttl_seconds={"crm": 60})
    app.get("/api/contacts", ws, params={"search": "ann"})
    app.creds("crm", ws=ws)
    assert app.get("/api/contacts", ws, params={"search": "ann"}).headers["x-cache"] == "miss"


def test_capabilities_share_the_cache(app, hub):
    ws = app.workspace()
    app.creds("docs", ws=ws)
    app.settings(ws, cache_ttl_seconds={"docs": 60})
    app.get("/api/docs/doc-2", ws)
    response = app.post("/api/capabilities/docs.get/invoke", ws, json={"input": {"id": "doc-2"}})
    assert response.status_code == 200
    assert len(tool_calls(hub, "docs", "get_doc")) == 1


def test_writes_and_ticket_lists_are_not_cached(app, hub):
    ws = app.workspace()
    app.creds("tickets", ws=ws)
    app.settings(ws, cache_ttl_seconds={"tickets": 60})
    app.get("/api/tickets", ws)
    app.post("/api/tickets", ws, json={"title": "New", "body": "x"})
    titles = [t["title"] for t in app.get("/api/tickets", ws).json()["tickets"]]
    assert titles == ["New"]
    assert len(tool_calls(hub, "tickets", "list_tickets")) == 2
