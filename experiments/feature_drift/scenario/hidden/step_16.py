import hashlib
import hmac
import json
import time
import uuid

import hub as h
from conftest import CREDS, error_code, tool_calls

SECRET = "whsec-test-1"


def setup(app):
    ws = app.workspace()
    response = app.put("/api/credentials/crm", ws, json={**CREDS["crm"], "webhook_secret": SECRET})
    assert response.status_code == 204
    app.creds("tickets", ws=ws)
    workflow = app.post(
        "/api/workflows",
        ws,
        json={
            "name": "Onboard",
            "steps": [
                {
                    "id": "ticket",
                    "capability": "tickets.create",
                    "input": {
                        "title": "Welcome {{params.name}}",
                        "body": "{{params.company}} / {{params.email}}",
                        "priority": "low",
                    },
                }
            ],
        },
    ).json()
    trigger = app.post(
        "/api/triggers",
        ws,
        json={
            "event": "crm.contact.created",
            "workflow_id": workflow["id"],
            "params": {
                "name": "{{event.name}}",
                "email": "{{event.email}}",
                "company": "{{event.company}}",
            },
        },
    )
    assert trigger.status_code == 201, trigger.text
    return ws, workflow, trigger.json()


def deliver(app, ws, payload, secret=SECRET, delivery=None):
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    headers = {
        "X-Pipe-Signature": signature,
        "X-Pipe-Delivery": delivery or uuid.uuid4().hex,
        "Content-Type": "application/json",
    }
    return app.post(f"/api/webhooks/crm/{ws}", content=raw, headers=headers)


def created(name="Mia Holm", email="mia@holm.test", org="Holm AS"):
    return {
        "event": "contact.created",
        "data": {"id": "c_new1", "full_name": name, "email": email, "org": {"name": org}},
    }


def wait_for_tickets(hub, count, timeout=6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = tool_calls(hub, "tickets", "create_ticket")
        if len(found) >= count:
            return found
        time.sleep(0.1)
    return tool_calls(hub, "tickets", "create_ticket")


def test_contact_created_runs_trigger(app, hub):
    ws, workflow, trigger = setup(app)
    response = deliver(app, ws, created())
    assert response.status_code == 202, response.text
    assert response.json() == {"accepted": True, "duplicate": False}
    found = wait_for_tickets(hub, 1)
    assert [c["params"]["arguments"] for c in found] == [
        {"title": "Welcome Mia Holm", "body": "Holm AS / mia@holm.test", "priority": "low"}
    ]
    deadline = time.monotonic() + 5
    runs = []
    while time.monotonic() < deadline and not runs:
        runs = app.get("/api/triggers/runs", ws).json()["runs"]
        time.sleep(0.1)
    assert runs[0]["trigger_id"] == trigger["id"] and runs[0]["workflow_id"] == workflow["id"]
    assert runs[0]["status"] == "succeeded"


def test_duplicate_delivery_runs_once(app, hub):
    ws, _, _ = setup(app)
    delivery = uuid.uuid4().hex
    assert deliver(app, ws, created(), delivery=delivery).json()["duplicate"] is False
    again = deliver(app, ws, created(), delivery=delivery)
    assert again.status_code == 202 and again.json() == {"accepted": True, "duplicate": True}
    time.sleep(1.0)
    assert len(wait_for_tickets(hub, 2, timeout=1.0)) == 1


def test_signatures(app, hub):
    ws, _, _ = setup(app)
    response = deliver(app, ws, created(), secret="wrong-secret")
    assert response.status_code == 401 and error_code(response) == "invalid_signature"
    raw = json.dumps(created()).encode()
    response = app.post(f"/api/webhooks/crm/{ws}", content=raw, headers={"X-Pipe-Delivery": "x1"})
    assert response.status_code == 401 and error_code(response) == "invalid_signature"
    plain = app.workspace()
    app.creds("crm", ws=plain)
    response = deliver(app, plain, created())
    assert response.status_code == 401 and error_code(response) == "invalid_signature"
    response = deliver(app, "missing-ws", created())
    assert response.status_code == 404 and error_code(response) == "unknown_workspace"
    time.sleep(0.5)
    assert tool_calls(hub, "tickets", "create_ticket") == []


def test_other_events_are_ignored(app, hub):
    ws, _, _ = setup(app)
    response = deliver(app, ws, {"event": "contact.deleted", "data": {"id": "c_1"}})
    assert response.status_code == 202
    time.sleep(0.5)
    assert tool_calls(hub, "tickets", "create_ticket") == []


def test_null_company_and_failed_runs(app, hub):
    ws, _, _ = setup(app)
    payload = created(org=None)
    payload["data"]["org"] = None
    assert deliver(app, ws, payload).status_code == 202
    assert wait_for_tickets(hub, 1)
    app.delete("/api/credentials/tickets", ws)
    assert deliver(app, ws, created(email="b@b.test")).status_code == 202
    deadline, statuses = time.monotonic() + 5, []
    while time.monotonic() < deadline and "failed" not in statuses:
        statuses = [r["status"] for r in app.get("/api/triggers/runs", ws).json()["runs"]]
        time.sleep(0.1)
    assert statuses[0] == "failed"


def test_trigger_validation_and_restart(app, hub):
    ws, workflow, trigger = setup(app)
    for body in (
        {"event": "crm.contact.created", "workflow_id": "missing", "params": {}},
        {"event": "crm.planet.exploded", "workflow_id": workflow["id"], "params": {}},
    ):
        response = app.post("/api/triggers", ws, json=body)
        assert response.status_code == 422 and error_code(response) == "invalid_request"
    app.restart()
    listed = app.get("/api/triggers", ws).json()
    rows = listed["triggers"] if isinstance(listed, dict) else listed
    assert any(t["id"] == trigger["id"] for t in rows)
    assert deliver(app, ws, created(name=h.FIRST[0] + " Restart")).status_code == 202
    assert wait_for_tickets(hub, 1)
