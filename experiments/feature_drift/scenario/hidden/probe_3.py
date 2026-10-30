import time

from conftest import error_code, fault


def test_per_integration_timeout(app, hub):
    ws = app.workspace()
    app.creds("crm", "weatherco", ws=ws)
    app.settings(ws, timeouts={"crm": 1})
    fault(hub, "crm", latency=2)
    started = time.monotonic()
    response = app.get("/api/contacts", ws)
    assert response.status_code == 504 and error_code(response) == "upstream_timeout"
    assert time.monotonic() - started < 1.9
    entry = app.audit(ws, integration="crm")[0]
    assert entry["outcome"] == "timeout" and entry["status"] is None
    fault(hub, "weatherco", latency=2)
    assert app.get("/api/weather", ws, params={"city": "Oslo"}).status_code == 200


def test_validation(app, hub):
    ws = app.workspace()
    for timeouts in ({"crm": 0}, {"crm": "slow"}, {"fax": 2}):
        response = app.put(f"/api/workspaces/{ws}/settings", json={"timeouts": timeouts})
        assert response.status_code == 422 and error_code(response) == "invalid_request", timeouts
