"""Request 13: webhook sinks with deterministic bodies and idempotency keys, off the write path."""

import hashlib
import hmac
import json
import time

from conftest import canonical, expect_error, uid, wait_for


def sink(app, hub, tenant, events=None, name=None):
    name = name or uid("s")
    body = {"id": name, "url": f"{hub.url}/sinks/{name}", "secret": "sec-" + name}
    if events is not None:
        body["events"] = events
    response = app.post("/api/sinks", tenant, json=body)
    assert response.status_code == 201, response.text
    return name


def received(hub, name):
    return hub.calls_to("sinks", f"/sinks/{name}")


def deliveries(app, tenant, name):
    response = app.get(f"/api/sinks/{name}/deliveries", tenant)
    assert response.status_code == 200, response.text
    return response.json()["deliveries"]


def test_sink_registration(app, hub):
    t = app.tenant()
    response = app.post(
        "/api/sinks", t, json={"id": "erp", "url": f"{hub.url}/sinks/erp", "secret": "s"}
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["id"] == "erp" and created["url"] == f"{hub.url}/sinks/erp"
    assert sorted(created["events"]) == ["branch.promoted", "grant.invalidated"]
    assert "secret" not in response.text and set(response.json()) == {"id", "url", "events"}
    sink(app, hub, t, events=["branch.promoted"], name="audit")
    listed = app.get("/api/sinks", t).json()["sinks"]
    assert [s["id"] for s in listed] == ["audit", "erp"] and "secret" not in json.dumps(listed)
    expect_error(
        app.post("/api/sinks", t, json={"id": "erp", "url": "http://x.test/", "secret": "s"}),
        409,
        "conflict",
    )
    for bad in (
        {"id": "x1", "url": "ftp://x.test/", "secret": "s"},
        {"id": "x2", "url": "not a url", "secret": "s"},
        {"id": "x3", "url": "http://x.test/", "secret": ""},
        {"id": "x4", "url": "http://x.test/", "secret": "s", "events": ["branch.deleted"]},
    ):
        expect_error(app.post("/api/sinks", t, json=bad), 422, "invalid_request")
    assert app.delete("/api/sinks/audit", t).status_code == 204
    expect_error(app.delete("/api/sinks/audit", t), 404, "not_found")
    expect_error(app.get("/api/sinks/audit/deliveries", t), 404, "not_found")
    assert [s["id"] for s in app.get("/api/sinks", t).json()["sinks"]] == ["erp"]


def test_promotion_is_delivered_after_commit(app, hub):
    t = app.tenant()
    name = sink(app, hub, t)
    only_grants = sink(app, hub, t, events=["grant.invalidated"])
    branch = app.fork(tenant=t)
    b = app.add("k", 1, 20, branch=branch, tenant=t)
    a = app.add("j", 1, 10, branch=branch, tenant=t)
    at = app.t()
    assert app.merge(branch, tenant=t, asserted_at=at).status_code == 200
    calls = wait_for(lambda: received(hub, name), timeout=5)
    assert len(calls) == 1
    expected = {
        "event": "branch.promoted",
        "tenant": t,
        "branch": branch,
        "asserted_at": at,
        "promoted": [a["id"], b["id"]],
    }
    call = calls[0]
    assert call["raw"] == canonical(expected)
    headers = call["headers"]
    assert headers["x-chronoweave-event"] == "branch.promoted"
    assert headers["idempotency-key"] == hashlib.sha256(call["raw"]).hexdigest()
    signature = hmac.new(f"sec-{name}".encode(), call["raw"], hashlib.sha256).hexdigest()
    assert headers["x-chronoweave-signature"] == f"sha256={signature}"
    assert headers["content-type"].startswith("application/json")
    rows = wait_for(lambda: [d for d in deliveries(app, t, name) if d["status"] == "delivered"])
    assert rows == [
        {
            "idempotency_key": headers["idempotency-key"],
            "event": "branch.promoted",
            "status": "delivered",
            "attempts": 1,
            "last_status": 200,
        }
    ]
    time.sleep(0.3)
    assert received(hub, only_grants) == [] and deliveries(app, t, only_grants) == []


def test_grant_invalidated_once_per_sealed_grant(app, hub):
    t = app.tenant()
    root = app.add("root", 1, 500, tenant=t)
    left = app.add("left", 1, 501, tenant=t, parents=[root["id"]])
    right = app.add("right", 1, 502, tenant=t, parents=[root["id"]])
    g1 = app.add(
        "grant:1", {"payee": "p", "amount": "1.00"}, 20, tenant=t, parents=[left["id"], right["id"]]
    )
    g2 = app.add("grant:2", {"payee": "p", "amount": "1.00"}, 10, tenant=t, parents=[root["id"]])
    app.add(
        "grant:3", {"payee": "p", "amount": "1.00"}, 300, tenant=t, parents=[root["id"]]
    )  # unsealed
    app.add("note", 1, 30, tenant=t, parents=[root["id"]])  # sealed, not a grant
    app.seal(0, 99, ident="qa", tenant=t)
    app.seal(15, 25, ident="qb", tenant=t)
    name = sink(app, hub, t)
    promotions_only = sink(app, hub, t, events=["branch.promoted"])
    at = app.t()
    assert app.retract(root["id"], tenant=t, asserted_at=at).status_code == 200
    calls = wait_for(lambda: len(received(hub, name)) >= 2 and received(hub, name), timeout=5)
    time.sleep(0.5)
    bodies = sorted(
        (json.loads(c["raw"]) for c in received(hub, name)), key=lambda b: b["assertion_id"]
    )
    assert bodies == sorted(
        [
            {
                "event": "grant.invalidated",
                "tenant": t,
                "assertion_id": g2["id"],
                "seal_ids": ["qa"],
                "cause": root["id"],
                "asserted_at": at,
            },
            {
                "event": "grant.invalidated",
                "tenant": t,
                "assertion_id": g1["id"],
                "seal_ids": ["qa", "qb"],
                "cause": root["id"],
                "asserted_at": at,
            },
        ],
        key=lambda b: b["assertion_id"],
    )
    rows = deliveries(app, t, name)
    assert [r["idempotency_key"] for r in rows] == [
        hashlib.sha256(canonical(b)).hexdigest()
        for b in (
            {
                "event": "grant.invalidated",
                "tenant": t,
                "assertion_id": g2["id"],
                "seal_ids": ["qa"],
                "cause": root["id"],
                "asserted_at": at,
            },
            {
                "event": "grant.invalidated",
                "tenant": t,
                "assertion_id": g1["id"],
                "seal_ids": ["qa", "qb"],
                "cause": root["id"],
                "asserted_at": at,
            },
        )
    ]
    assert received(hub, promotions_only) == []
    # already tainted grants are not "newly" invalidated by a second retraction
    assert app.retract(left["id"], tenant=t).status_code == 200
    time.sleep(1.0)
    assert len(received(hub, name)) == 2
    assert calls  # first wait succeeded


def test_retries_reuse_body_and_key(app, hub):
    t = app.tenant()
    name = sink(app, hub, t)
    hub.fault(f"sink:{name}", fail_next=2, fail_status=503)
    branch = app.fork(tenant=t)
    app.add("k", 1, 1, branch=branch, tenant=t)
    assert app.merge(branch, tenant=t).status_code == 200
    rows = wait_for(
        lambda: [d for d in deliveries(app, t, name) if d["status"] == "delivered"], timeout=10
    )
    assert rows and rows[0]["attempts"] == 3 and rows[0]["last_status"] == 200
    calls = received(hub, name)
    assert len(calls) == 3
    assert (
        len({c["raw"] for c in calls}) == 1
        and len({c["headers"]["idempotency-key"] for c in calls}) == 1
    )
    assert calls[1]["at"] - calls[0]["at"] >= 0.8


def test_slow_sink_never_delays_writes(app, hub):
    t = app.tenant()
    name = sink(app, hub, t)
    hub.fault(f"sink:{name}", latency=3)
    branch = app.fork(tenant=t)
    app.add("k", 1, 1, branch=branch, tenant=t)
    started = time.monotonic()
    assert app.merge(branch, tenant=t).status_code == 200
    app.add("other", 1, 1, tenant=t)
    assert time.monotonic() - started < 2.5  # a blocked write would wait for the 3 s sink
    rows = wait_for(
        lambda: [d for d in deliveries(app, t, name) if d["status"] == "delivered"], timeout=8
    )
    assert rows and rows[0]["attempts"] == 1
