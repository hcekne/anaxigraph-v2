import hub as h
from conftest import calls, error_code


def expected(start, end, dataset="a"):
    rows = [e for e in h.events(dataset) if start <= e["starts_at"][:10] <= end]
    rows.sort(key=lambda e: (e["starts_at"], e["uid"]))
    return [
        {
            "id": e["uid"],
            "title": e["summary"],
            "start": e["starts_at"],
            "end": e["ends_at"],
            "location": e["where"],
        }
        for e in rows
    ]


def test_events_across_pages(app, hub):
    app.creds("calendar")
    response = app.get("/api/calendar/events", params={"from": "2026-01-10", "to": "2026-01-21"})
    assert response.status_code == 200, response.text
    events = expected("2026-01-10", "2026-01-21")
    assert len(events) > 5
    assert response.json() == {"events": events}


def test_narrow_range(app, hub):
    app.creds("calendar")
    body = app.get("/api/calendar/events", params={"from": "2026-01-12", "to": "2026-01-13"}).json()
    assert body == {"events": expected("2026-01-12", "2026-01-13")}


def test_token_is_reused(app, hub):
    app.creds("calendar")
    with hub.lock:
        hub.tokens.clear()
    params = {"from": "2026-01-10", "to": "2026-01-12"}
    assert app.get("/api/calendar/events", params=params).status_code == 200
    assert app.get("/api/calendar/events", params=params).status_code == 200
    assert len(calls(hub, "calendar", "/oauth/token")) == 1


def test_revoked_token_is_replaced(app, hub):
    app.creds("calendar")
    params = {"from": "2026-01-10", "to": "2026-01-12"}
    assert app.get("/api/calendar/events", params=params).status_code == 200
    before = len(calls(hub, "calendar", "/oauth/token"))  # 0 when an earlier test's token is cached
    with hub.lock:
        hub.tokens.clear()
    response = app.get("/api/calendar/events", params=params)
    assert response.status_code == 200 and response.json() == {"events": expected(*params.values())}
    assert len(calls(hub, "calendar", "/oauth/token")) - before == 1


def test_validation(app, hub):
    app.creds("calendar")
    for params in (
        {"from": "2026-01-10"},
        {"from": "2026-01-12", "to": "2026-01-10"},
        {"from": "2026-01-01", "to": "2026-02-05"},
        {"from": "soon", "to": "2026-01-10"},
    ):
        response = app.get("/api/calendar/events", params=params)
        assert response.status_code == 422 and error_code(response) == "invalid_request", params


def test_bad_client_and_overview(fresh_app, hub):
    params = {"from": "2026-01-10", "to": "2026-01-12"}
    response = fresh_app.get("/api/calendar/events", params=params)
    assert response.status_code == 409 and error_code(response) == "not_configured"
    row = fresh_app.get("/api/integrations/calendar").json()
    assert row["kind"] == "rest" and row["status"] == "unconfigured"
    fresh_app.put("/api/credentials/calendar", json={"client_id": "x", "client_secret": "y"})
    response = fresh_app.get("/api/calendar/events", params=params)
    assert response.status_code == 502 and error_code(response) == "upstream_auth"
    fresh_app.creds("calendar")
    assert fresh_app.get("/api/integrations/calendar").json()["status"] == "ok"
