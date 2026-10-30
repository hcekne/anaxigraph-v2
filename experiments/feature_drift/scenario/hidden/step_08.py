from conftest import calls, error_code, fault, tool_calls


def new_entries(app, before, **params):
    known = {e["id"] for e in before}
    return [e for e in app.audit(**params) if e["id"] not in known]


def test_create_and_list_tickets(app, hub):
    app.creds("tickets")
    response = app.post(
        "/api/tickets", json={"title": "Boiler leak", "body": "North wing", "priority": "high"}
    )
    assert response.status_code == 201, response.text
    ticket = response.json()
    assert ticket["id"].startswith("T-") and ticket["status"] == "open" and ticket["url"]
    listed = app.get("/api/tickets", params={"status": "open"}).json()["tickets"]
    assert {
        "id": ticket["id"],
        "title": "Boiler leak",
        "priority": "high",
        "status": "open",
    } in listed
    assert app.get("/api/tickets").json()["tickets"] == listed
    sent = tool_calls(hub, "tickets", "create_ticket")[0]["params"]["arguments"]
    assert sent == {"title": "Boiler leak", "body": "North wing", "priority": "high"}


def test_ticket_defaults_and_validation(app, hub):
    app.creds("tickets")
    response = app.post("/api/tickets", json={"title": "T", "body": "B"})
    assert response.status_code == 201
    assert (
        tool_calls(hub, "tickets", "create_ticket")[0]["params"]["arguments"]["priority"]
        == "normal"
    )
    for body in ({"body": "B"}, {"title": "T"}, {"title": "T", "body": "B", "priority": "urgent"}):
        response = app.post("/api/tickets", json=body)
        assert response.status_code == 422 and error_code(response) == "invalid_request"


def test_tickets_in_overview(app, hub):
    app.creds("tickets")
    row = app.get("/api/integrations/tickets").json()
    assert row["kind"] == "mcp" and row["status"] == "ok"


def test_one_audit_entry_per_provider_request(app, hub):
    app.creds("weatherco", "crm", "docs", "tickets")
    cases = [
        ("weatherco", lambda: app.get("/api/weather", params={"city": "Oslo"})),
        ("crm", lambda: app.get("/api/contacts")),
        ("docs", lambda: app.get("/api/docs/search", params={"q": "deploy"})),
        ("tickets", lambda: app.post("/api/tickets", json={"title": "A", "body": "B"})),
    ]
    for name, action in cases:
        before, sent = app.audit(), len(calls(hub, name))
        assert action().status_code in (200, 201)
        added = new_entries(app, before)
        assert added and {e["integration"] for e in added} == {name}, (name, added)
        assert len(added) == len(calls(hub, name)) - sent, name
        for entry in added:
            assert entry["outcome"] == "ok" and entry["status"] in (200, 201, 202)
            assert isinstance(entry["duration_ms"], (int, float)) and entry["duration_ms"] >= 0
            assert entry["operation"] and entry["at"]


def test_retries_and_tokens_are_audited(app, hub):
    app.creds("ledger", "calendar")
    fault(hub, "ledger", rate_limit_next=2, retry_after=0)
    before = app.audit()
    assert app.get("/api/invoices").status_code == 200
    added = new_entries(app, before, integration="ledger")
    assert sorted(e["status"] for e in added if e["outcome"] == "error") == [429, 429]
    assert len(added) == len(calls(hub, "ledger"))
    before = app.audit()
    with hub.lock:
        hub.tokens.clear()
    app.get("/api/calendar/events", params={"from": "2026-01-10", "to": "2026-01-11"})
    added = new_entries(app, before, integration="calendar")
    assert len(added) == len(calls(hub, "calendar"))


def test_failures_are_audited(app, hub):
    app.put("/api/credentials/weatherco", json={"api_key": "wrong"})
    before = app.audit()
    app.get("/api/weather", params={"city": "Oslo"})
    added = new_entries(app, before)
    assert added and all(e["outcome"] == "error" for e in added)
    assert 401 in {e["status"] for e in added}
    app.creds("weatherco")


def test_order_filters_and_limits(app, hub):
    app.creds("weatherco", "crm")
    app.get("/api/weather", params={"city": "Oslo"})
    app.get("/api/contacts", params={"search": "ann"})
    entries = app.audit()
    stamps = [e["at"] for e in entries]
    assert stamps == sorted(stamps, reverse=True)
    assert entries[0]["integration"] == "crm"
    assert len(app.audit(limit=2)) == 2
    assert {e["integration"] for e in app.audit(integration="crm")} == {"crm"}
    assert app.audit(integration="nothing-here") == []
    for limit in (0, 501):
        response = app.get("/api/audit", params={"limit": limit})
        assert response.status_code == 422 and error_code(response) == "invalid_request"
