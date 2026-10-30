import json

from conftest import SECRETS, error_code

EMAIL = "ann.berg0@a-example.com"


def test_entries_record_redacted_requests(app, hub):
    app.creds("fxrates", "crm")
    app.get("/api/fx/convert", params={"amount": "1", "from": "USD", "to": "EUR"})
    entry = app.audit(integration="fxrates")[0]
    assert entry["request"]["method"] == "GET"
    assert entry["request"]["path"] == "/fxrates/latest.json"
    assert entry["request"]["query"]["app_id"] == "[REDACTED]"
    app.get("/api/contacts", params={"search": EMAIL})
    entry = app.audit(integration="crm")[0]
    assert entry["request"]["path"] == "/crm/api/contacts"
    assert entry["request"]["query"]["search"] == "[EMAIL]"


def test_export_contains_no_secrets_or_emails(app, hub):
    app.creds(
        "weatherco", "crm", "docs", "calendar", "ledger", "tickets", "fxrates", "lingo", "shiptrack"
    )
    app.get("/api/weather", params={"city": "Oslo"})
    app.get("/api/contacts", params={"search": EMAIL})
    app.post("/api/contacts", json={"name": "Pii Person", "email": "pii.person@secret.test"})
    app.get("/api/docs/search", params={"q": "deploy"})
    app.get("/api/calendar/events", params={"from": "2026-01-10", "to": "2026-01-11"})
    app.get("/api/invoices")
    app.post("/api/tickets", json={"title": "x", "body": "y"})
    app.get("/api/fx/convert", params={"amount": "1", "from": "USD", "to": "EUR"})
    app.post(
        "/api/capabilities/lingo.translate/invoke",
        json={"input": {"text": "hello", "target": "es"}},
    )
    app.get("/api/shipments/SH123")
    app.get("/api/integrations")
    response = app.get("/api/audit/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    text = response.text
    for secret in SECRETS:
        assert secret not in text, secret
    assert EMAIL not in text and "pii.person@secret.test" not in text
    listed = app.get("/api/audit", params={"limit": 500}).json()
    for secret in SECRETS:
        assert secret not in json.dumps(listed), secret


def test_export_order_and_filter(app, hub):
    app.creds("weatherco", "crm")
    app.get("/api/weather", params={"city": "Oslo"})
    app.get("/api/contacts", params={"search": "x"})
    rows = [json.loads(line) for line in app.get("/api/audit/export").text.splitlines() if line]
    newest_first = app.audit()
    assert [r["id"] for r in rows][-len(newest_first) :] == [
        e["id"] for e in reversed(newest_first)
    ]
    crm = [
        json.loads(line)
        for line in app.get("/api/audit/export", params={"integration": "crm"}).text.splitlines()
        if line
    ]
    assert crm and {r["integration"] for r in crm} == {"crm"}


def test_retention(app, hub):
    ws = app.workspace()
    app.creds("weatherco", ws=ws)
    app.settings(ws, audit_retention=10)
    for city in ["Oslo", "Paris", "Berlin", "Tokyo", "London", "Geneva"] * 2:
        app.get("/api/weather", ws, params={"city": city})
    entries = app.audit(ws)
    assert len(entries) == 10
    response = app.put(f"/api/workspaces/{ws}/settings", json={"audit_retention": 5})
    assert response.status_code == 422 and error_code(response) == "invalid_request"
