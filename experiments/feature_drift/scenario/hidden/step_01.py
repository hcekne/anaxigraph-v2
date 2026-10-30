import hub as h
from conftest import calls, close, error_code, fault


def test_forecast_matches_provider(app, hub):
    app.creds("weatherco")
    response = app.get("/api/weather", params={"city": "Oslo", "days": 3})
    assert response.status_code == 200, response.text
    assert close(response.json(), {"city": "Oslo", "country": "NO", "days": h.forecast("oslo", 3)})


def test_days_default_and_range(app, hub):
    app.creds("weatherco")
    assert len(app.get("/api/weather", params={"city": "paris"}).json()["days"]) == 3
    body = app.get("/api/weather", params={"city": " Tokyo ", "days": 7}).json()
    assert body["city"] == "Tokyo" and close(body["days"], h.forecast("tokyo", 7))
    for days in (0, 8, "x"):
        response = app.get("/api/weather", params={"city": "Oslo", "days": days})
        assert response.status_code == 422 and error_code(response) == "invalid_request"
    response = app.get("/api/weather")
    assert response.status_code == 422 and error_code(response) == "invalid_request"


def test_unknown_city(app, hub):
    app.creds("weatherco")
    response = app.get("/api/weather", params={"city": "Atlantis"})
    assert response.status_code == 404 and error_code(response) == "unknown_city"


def test_rejected_key_and_provider_failure(app, hub):
    assert app.put("/api/credentials/weatherco", json={"api_key": "wrong"}).status_code == 204
    response = app.get("/api/weather", params={"city": "Oslo"})
    assert response.status_code == 502 and error_code(response) == "upstream_auth"
    app.creds("weatherco")
    fault(hub, "weatherco", fail_next=20, fail_status=500)
    response = app.get("/api/weather", params={"city": "Oslo"})
    assert response.status_code == 502 and error_code(response) == "upstream_error"


def test_key_is_sent_and_never_returned(app, hub):
    response = app.put("/api/credentials/weatherco", json={"api_key": "wc-key-2"})
    assert response.status_code == 204 and "wc-key-2" not in response.text
    assert app.get("/api/weather", params={"city": "Berlin"}).status_code == 200
    assert {c["headers"].get("x-api-key") for c in calls(hub, "weatherco")} == {"wc-key-2"}
    for body in ({}, {"api_key": ""}):
        response = app.put("/api/credentials/weatherco", json=body)
        assert response.status_code == 422 and error_code(response) == "invalid_request"


def test_not_configured(fresh_app, hub):
    response = fresh_app.get("/api/weather", params={"city": "Oslo"})
    assert response.status_code == 409 and error_code(response) == "not_configured"
    assert calls(hub, "weatherco") == []
