import hub as h
from conftest import calls, close, error_code


def test_settings_defaults_and_validation(app, hub):
    ws = app.workspace()
    response = app.get(f"/api/workspaces/{ws}/settings")
    assert response.status_code == 200 and response.json()["weather_provider"] == "weatherco"
    updated = app.settings(ws, weather_provider="skycast")
    assert updated["weather_provider"] == "skycast"
    assert app.get(f"/api/workspaces/{ws}/settings").json() == updated
    response = app.put(f"/api/workspaces/{ws}/settings", json={"weather_provider": "sunshine"})
    assert response.status_code == 422 and error_code(response) == "invalid_request"
    response = app.put("/api/workspaces/missing-ws/settings", json={"weather_provider": "skycast"})
    assert response.status_code == 404 and error_code(response) == "not_found"


def test_weatherco_uses_v2(app, hub):
    app.creds("weatherco")
    response = app.get("/api/weather", params={"city": "Tromso", "days": 4})
    assert response.status_code == 200, response.text
    assert close(
        response.json(), {"city": "Tromso", "country": "NO", "days": h.forecast("tromso", 4)}
    )
    assert calls(hub, "weatherco", "/v2/forecast")
    response = app.get("/api/weather", params={"city": "Atlantis"})
    assert response.status_code == 404 and error_code(response) == "unknown_city"


def test_skycast_workspace(app, hub):
    ws = app.workspace()
    app.settings(ws, weather_provider="skycast")
    response = app.get("/api/weather", ws, params={"city": "Oslo"})
    assert response.status_code == 409 and error_code(response) == "not_configured"
    app.creds("skycast", ws=ws)
    response = app.get("/api/weather", ws, params={"city": "London", "days": 5})
    assert response.status_code == 200, response.text
    assert close(
        response.json(), {"city": "London", "country": "GB", "days": h.forecast("london", 5)}
    )
    assert calls(hub, "skycast") and not calls(hub, "weatherco")
    assert calls(hub, "skycast")[0]["headers"]["authorization"] == "Bearer sky-token-1"
    response = app.get("/api/weather", ws, params={"city": "Atlantis"})
    assert response.status_code == 404 and error_code(response) == "unknown_city"


def test_capabilities_and_overview(app, hub):
    ws = app.workspace()
    app.settings(ws, weather_provider="skycast")
    app.creds("skycast", ws=ws)
    names = {r["name"] for r in app.get("/api/capabilities", ws).json()["capabilities"]}
    assert {"weather.forecast", "skycast.forecast", "weatherco.forecast"} <= names
    output = app.post(
        "/api/capabilities/weather.forecast/invoke",
        ws,
        json={"input": {"city": "Paris", "days": 2}},
    ).json()["output"]
    assert close(output, {"city": "Paris", "country": "FR", "days": h.forecast("paris", 2)})
    output = app.post(
        "/api/capabilities/skycast.forecast/invoke", ws, json={"input": {"city": "Paris"}}
    ).json()["output"]
    assert close(output["days"], h.forecast("paris", 3))
    row = app.get("/api/integrations/skycast", ws).json()
    assert row["kind"] == "rest" and row["status"] == "ok"


def test_settings_survive_restart(app, hub):
    ws = app.workspace()
    app.settings(ws, weather_provider="skycast")
    app.restart()
    assert app.get(f"/api/workspaces/{ws}/settings").json()["weather_provider"] == "skycast"
