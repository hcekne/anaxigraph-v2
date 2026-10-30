import time

from conftest import calls, error_code, fault


def test_circuit_opens_and_recovers(app, hub):
    ws = app.workspace()
    app.creds("crm", ws=ws)
    app.settings(ws, breaker={"threshold": 2, "cooldown_seconds": 1})
    fault(hub, "crm", fail_next=1000, fail_status=500)
    assert app.get("/api/contacts", ws).status_code == 502
    assert app.get("/api/contacts", ws).status_code == 502
    sent = len(calls(hub, "crm"))
    used = app.get("/api/usage", ws).json()["integrations"]["crm"]["used"]
    response = app.get("/api/contacts", ws)
    assert response.status_code == 503 and error_code(response) == "circuit_open"
    row = app.get("/api/integrations/crm", ws).json()
    assert row["status"] == "degraded" and row["detail"]
    assert len(calls(hub, "crm")) == sent
    assert app.get("/api/usage", ws).json()["integrations"]["crm"]["used"] == used
    time.sleep(1.2)
    fault(hub, "crm", fail_next=0)
    assert app.get("/api/contacts", ws).status_code == 200
    assert app.get("/api/integrations/crm", ws).json()["status"] == "ok"


def test_half_open_failure_reopens(app, hub):
    ws = app.workspace()
    app.creds("docs", ws=ws)
    app.settings(ws, breaker={"threshold": 1, "cooldown_seconds": 1})
    fault(hub, "docs", fail_next=1000, fail_status=503)
    assert app.get("/api/docs/search", ws, params={"q": "x"}).status_code == 502
    assert error_code(app.get("/api/docs/search", ws, params={"q": "x"})) == "circuit_open"
    time.sleep(1.2)
    assert app.get("/api/docs/search", ws, params={"q": "x"}).status_code == 502
    assert error_code(app.get("/api/docs/search", ws, params={"q": "x"})) == "circuit_open"


def test_client_errors_and_successes_do_not_trip(app, hub):
    ws = app.workspace()
    app.creds("weatherco", ws=ws)
    app.settings(ws, breaker={"threshold": 2, "cooldown_seconds": 30})
    for _ in range(3):
        assert (
            error_code(app.get("/api/weather", ws, params={"city": "Atlantis"})) == "unknown_city"
        )
    fault(hub, "weatherco", fail_next=1, fail_status=500)
    assert app.get("/api/weather", ws, params={"city": "Oslo"}).status_code == 502
    assert app.get("/api/weather", ws, params={"city": "Oslo"}).status_code == 200
    fault(hub, "weatherco", fail_next=1, fail_status=500)
    assert app.get("/api/weather", ws, params={"city": "Oslo"}).status_code == 502
    assert app.get("/api/weather", ws, params={"city": "Oslo"}).status_code == 200


def test_breaker_is_off_by_default_and_validated(app, hub):
    ws = app.workspace()
    app.creds("crm", ws=ws)
    fault(hub, "crm", fail_next=1000, fail_status=500)
    for _ in range(5):
        assert app.get("/api/contacts", ws).status_code == 502
    for breaker in (
        {"threshold": 0, "cooldown_seconds": 1},
        {"threshold": 2, "cooldown_seconds": 0},
    ):
        response = app.put(f"/api/workspaces/{ws}/settings", json={"breaker": breaker})
        assert response.status_code == 422 and error_code(response) == "invalid_request"


def test_timeouts(app, hub):
    ws = app.workspace()
    app.creds("weatherco", ws=ws)
    fault(hub, "weatherco", latency=6.5)
    started = time.monotonic()
    response = app.get("/api/weather", ws, params={"city": "Oslo"})
    elapsed = time.monotonic() - started
    assert response.status_code == 504 and error_code(response) == "upstream_timeout"
    assert elapsed < 6.4
    entry = app.audit(ws)[0]
    assert (
        entry["outcome"] in ("error", "timeout") and entry["status"] is None
    )  # probe 3 renames it
