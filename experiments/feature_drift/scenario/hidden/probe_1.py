from conftest import calls, error_code


def invoke(app, payload, ws=None):
    return app.post("/api/capabilities/geo.lookup/invoke", ws, json={"input": payload})


def test_lookup(app, hub):
    ws = app.workspace()
    app.creds("geo", ws=ws)
    response = invoke(app, {"address": "Karl Johans gate 1, Oslo"}, ws)
    assert response.status_code == 200, response.text
    assert response.json()["output"] == {
        "address": "Karl Johans gate 1, Oslo",
        "lat": 59.91,
        "lon": 10.75,
        "label": "Oslo",
    }
    assert calls(hub, "geo")[0]["headers"]["x-geo-key"] == "geo-key-1"
    response = invoke(app, {"address": "Nowhere"}, ws)
    assert response.status_code == 404 and error_code(response) == "not_found"
    assert invoke(app, {}, ws).status_code == 422


def test_platform_rules(app, hub):
    ws = app.workspace()
    row = app.get("/api/integrations/geo", ws).json()
    assert row["kind"] == "rest" and row["status"] == "unconfigured"
    response = invoke(app, {"address": "Paris"}, ws)
    assert response.status_code == 409 and error_code(response) == "not_configured"
    app.creds("geo", ws=ws)
    assert app.get("/api/integrations/geo", ws).json()["status"] == "ok"
    app.settings(ws, quotas={"geo": 2}, cache_ttl_seconds={"geo": 60})
    before = len(calls(hub, "geo"))
    invoke(app, {"address": "Paris"}, ws)
    invoke(app, {"address": "Paris"}, ws)
    assert len(calls(hub, "geo")) - before == 1
    assert invoke(app, {"address": "Berlin"}, ws).status_code == 200
    response = invoke(app, {"address": "Tokyo"}, ws)
    assert response.status_code == 429 and error_code(response) == "quota_exceeded"
    assert {e["integration"] for e in app.audit(ws)} == {"geo"}
    assert "geo-key-1" not in app.get("/api/audit/export", ws).text
    app.settings(ws, disabled_integrations=["geo"])
    response = invoke(app, {"address": "Paris"}, ws)
    assert response.status_code == 403 and error_code(response) == "integration_disabled"


def test_breaker_and_auth(app, hub):
    ws = app.workspace()
    app.put("/api/credentials/geo", ws, json={"api_key": "bad"})
    response = invoke(app, {"address": "Paris"}, ws)
    assert response.status_code == 502 and error_code(response) == "upstream_auth"
    app.creds("geo", ws=ws)
    app.settings(ws, breaker={"threshold": 1, "cooldown_seconds": 30})
    from conftest import fault

    fault(hub, "geo", fail_next=100, fail_status=500)
    assert invoke(app, {"address": "Paris"}, ws).status_code == 502
    assert error_code(invoke(app, {"address": "Paris"}, ws)) == "circuit_open"
