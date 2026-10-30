import hub as h
from conftest import error_code, tool_calls

FIRST = h.contacts("a")[0]


def raise_follow_up():
    return {
        "name": "Raise follow-up",
        "steps": [
            {
                "id": "find",
                "capability": "crm.search_contacts",
                "input": {"search": "{{params.name}}", "limit": "{{params.limit}}"},
            },
            {
                "id": "raise",
                "capability": "tickets.create",
                "input": {
                    "title": "Call {{steps.find.output.contacts.0.name}}",
                    "body": "{{steps.find.output.contacts.0.email}}",
                    "priority": "normal",
                },
            },
        ],
    }


def create(app, ws, definition):
    response = app.post("/api/workflows", ws, json=definition)
    assert response.status_code == 201, response.text
    return response.json()


def test_run_chains_steps(app, hub):
    ws = app.workspace()
    app.creds("crm", "tickets", ws=ws)
    workflow = create(app, ws, raise_follow_up())
    assert workflow["id"] and workflow["name"] == "Raise follow-up"
    response = app.post(
        f"/api/workflows/{workflow['id']}/run",
        ws,
        json={"params": {"name": FIRST["email"], "limit": 1}},
    )
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "succeeded"
    assert [s["id"] for s in run["steps"]] == ["find", "raise"]
    assert all(s["status"] == "succeeded" for s in run["steps"])
    assert len(run["steps"][0]["output"]["contacts"]) == 1
    sent = tool_calls(hub, "tickets", "create_ticket")[0]["params"]["arguments"]
    assert sent == {
        "title": f"Call {FIRST['full_name']}",
        "body": FIRST["email"],
        "priority": "normal",
    }
    assert run["steps"][1]["output"]["id"].startswith("T-")


def test_whole_templates_keep_their_type(app, hub):
    ws = app.workspace()
    app.creds("crm", "tickets", ws=ws)
    workflow = create(app, ws, raise_follow_up())
    run = app.post(
        f"/api/workflows/{workflow['id']}/run", ws, json={"params": {"name": "", "limit": 3}}
    ).json()
    assert run["status"] == "succeeded"
    assert len(run["steps"][0]["output"]["contacts"]) == 3


def test_failures_skip_later_steps(app, hub):
    ws = app.workspace()
    app.creds("crm", ws=ws)
    workflow = create(app, ws, raise_follow_up())
    run = app.post(
        f"/api/workflows/{workflow['id']}/run", ws, json={"params": {"name": "zzzz", "limit": 1}}
    ).json()
    assert run["status"] == "failed"
    assert run["steps"][0]["status"] == "succeeded"
    assert run["steps"][1]["status"] == "failed"
    assert run["steps"][1]["error"]["code"] == "template_error"
    definition = raise_follow_up()
    definition["steps"].insert(
        0, {"id": "weather", "capability": "weatherco.forecast", "input": {"city": "Oslo"}}
    )
    workflow = create(app, ws, definition)
    run = app.post(
        f"/api/workflows/{workflow['id']}/run", ws, json={"params": {"name": "ann", "limit": 1}}
    ).json()
    assert run["status"] == "failed"
    assert [s["status"] for s in run["steps"]] == ["failed", "skipped", "skipped"]
    assert run["steps"][0]["error"]["code"] == "not_configured"


def test_validation(app, hub):
    ws = app.workspace()
    bad = []
    definition = raise_follow_up()
    definition["steps"][0]["capability"] = "crm.nothing"
    bad.append(definition)
    definition = raise_follow_up()
    definition["steps"][1]["id"] = "find"
    bad.append(definition)
    definition = raise_follow_up()
    definition["steps"].reverse()
    bad.append(definition)
    bad.append({"name": "Empty", "steps": []})
    for definition in bad:
        response = app.post("/api/workflows", ws, json=definition)
        assert response.status_code == 422 and error_code(response) == "invalid_request"


def test_listing_scope_and_restart(app, hub):
    ws, other = app.workspace(), app.workspace()
    workflow = create(app, ws, raise_follow_up())
    assert {"id": workflow["id"], "name": "Raise follow-up"} in app.get(
        "/api/workflows", ws
    ).json()["workflows"]
    assert app.get("/api/workflows", other).json()["workflows"] == []
    response = app.get(f"/api/workflows/{workflow['id']}", other)
    assert response.status_code == 404 and error_code(response) == "not_found"
    response = app.post("/api/workflows/missing/run", ws, json={"params": {}})
    assert response.status_code == 404
    app.restart()
    fetched = app.get(f"/api/workflows/{workflow['id']}", ws).json()
    assert fetched["name"] == "Raise follow-up" and len(fetched["steps"]) == 2
