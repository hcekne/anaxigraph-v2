"""Request 16: remote witness overrides behind a per-tenant circuit breaker with cached certificates."""

import threading
import time

from conftest import expect_error, uid


def setup(app, hub, **budgets):
    t = app.tenant()
    app.seal(0, 999, ident="q1", tenant=t)
    app.seal(5000, 5999, ident="q2", tenant=t)
    return t


def witness_write(app, t, witness, effective=10, seal="q1", **kw):
    return app.write(
        "k", uid(), effective, tenant=t, override={"seal_id": seal, "witness": witness}, **kw
    )


def certify_calls(hub, t):
    return hub.calls_to("authority", f"/tenants/{t}/certify")


def status(app, t):
    response = app.get("/api/authority/status", t)
    assert response.status_code == 200, response.text
    return response.json()


def test_witness_overrides(app, hub):
    t = setup(app, hub)
    hub.approve(t, "w-ok", 10**9)
    ident = uid("a-")
    response = app.write(
        "k", 1, 10, id=ident, tenant=t, override={"seal_id": "q1", "witness": "w-ok"}
    )
    assert response.status_code == 201, response.text
    calls = certify_calls(hub, t)
    assert len(calls) == 1
    assert calls[0]["body"] == {
        "seal_id": "q1",
        "witness": "w-ok",
        "action": "assert",
        "assertion_id": ident,
    }
    expect_error(witness_write(app, t, "w-unknown"), 403, "invalid_override")
    expect_error(
        witness_write(app, t, "w-ok", seal="q2"), 403, "invalid_override"
    )  # q2 does not cover 10
    response = app.retract(ident, tenant=t, override={"seal_id": "q1", "witness": "w-ok"})
    assert response.status_code == 200, response.text
    assert certify_calls(hub, t)[-1]["body"]["action"] == "retract"
    before = len(certify_calls(hub, t))
    assert witness_write(app, t, "w-ok", effective=2000).status_code == 201  # needs no override
    assert len(certify_calls(hub, t)) == before
    assert status(app, t) == {
        "breaker": "closed",
        "consecutive_failures": 0,
        "degraded": False,
        "cached_certificates": 1,
    }


def test_failures_fall_back_to_cached_certificates(app, hub):
    t = setup(app, hub)
    hub.approve(t, "w-cached", app.now + 10_000)
    assert witness_write(app, t, "w-cached").status_code == 201
    hub.fault(f"authority:{t}", fail_next=3, fail_status=500)
    response = witness_write(app, t, "w-cached")
    assert response.status_code == 201, (
        response.text
    )  # partition: the remembered certificate decides
    before = app.state(tenant=t)
    hub.approve(t, "w-new", 10**9)
    expect_error(witness_write(app, t, "w-new"), 503, "authority_unavailable")
    assert app.state(tenant=t) == before
    expired_at = app.now + 20_000
    expect_error(
        witness_write(app, t, "w-cached", asserted_at=expired_at), 503, "authority_unavailable"
    )
    assert status(app, t)["consecutive_failures"] == 3


def test_breaker_opens_and_recovers(app, hub):
    t = setup(app, hub)
    expect_error(
        app.put("/api/authority/breaker", t, json={"threshold": 0, "cooldown_seconds": 1}),
        422,
        "invalid_request",
    )
    expect_error(
        app.put("/api/authority/breaker", t, json={"threshold": 2, "cooldown_seconds": 0}),
        422,
        "invalid_request",
    )
    response = app.put("/api/authority/breaker", t, json={"threshold": 2, "cooldown_seconds": 1})
    assert response.status_code == 200 and response.json() == {
        "threshold": 2,
        "cooldown_seconds": 1,
    }
    hub.approve(t, "w1", 10**9)
    assert witness_write(app, t, "w1").status_code == 201
    hub.fault(f"authority:{t}", fail_next=100, fail_status=502)
    expect_error(witness_write(app, t, "w2"), 503, "authority_unavailable")
    expect_error(witness_write(app, t, "w2"), 503, "authority_unavailable")
    assert status(app, t) == {
        "breaker": "open",
        "consecutive_failures": 2,
        "degraded": True,
        "cached_certificates": 1,
    }
    calls = len(certify_calls(hub, t))
    assert witness_write(app, t, "w1").status_code == 201  # cache, without calling
    expect_error(witness_write(app, t, "w2"), 503, "authority_unavailable")
    assert len(certify_calls(hub, t)) == calls
    # reads and writes that need no witness keep working while degraded
    assert app.write("free", 1, 3000, tenant=t).status_code == 201
    assert "free" in app.state(tenant=t)
    time.sleep(1.2)
    assert status(app, t)["breaker"] == "half_open" and status(app, t)["degraded"] is True
    expect_error(witness_write(app, t, "w2"), 503, "authority_unavailable")  # trial fails: reopen
    assert len(certify_calls(hub, t)) == calls + 1 and status(app, t)["breaker"] == "open"
    time.sleep(1.2)
    hub.reset()
    hub.approve(t, "w2", 10**9)
    assert witness_write(app, t, "w2").status_code == 201
    assert status(app, t) == {
        "breaker": "closed",
        "consecutive_failures": 0,
        "degraded": False,
        "cached_certificates": 2,
    }


def test_noisy_tenant_does_not_affect_others(app, hub):
    noisy, quiet = setup(app, hub), setup(app, hub)
    for t in (noisy, quiet):
        hub.approve(t, "w", 10**9)
    hub.fault(f"authority:{noisy}", fail_next=100, fail_status=500)
    for _ in range(3):
        expect_error(witness_write(app, noisy, "w"), 503, "authority_unavailable")
    assert status(app, noisy)["breaker"] == "open"
    assert witness_write(app, quiet, "w").status_code == 201
    assert status(app, quiet) == {
        "breaker": "closed",
        "consecutive_failures": 0,
        "degraded": False,
        "cached_certificates": 1,
    }
    assert status(app, noisy)["cached_certificates"] == 0
    # a hanging authority call for one tenant does not hold up another tenant's writes
    slow = setup(app, hub)
    hub.fault(f"authority:{slow}", latency=3)
    result = {}
    started = time.monotonic()
    thread = threading.Thread(target=lambda: result.update(r=witness_write(app, slow, "w")))
    thread.start()
    time.sleep(0.3)
    other = time.monotonic()
    assert witness_write(app, quiet, "w").status_code == 201
    assert app.write("plain", 1, 3000, tenant=quiet).status_code == 201
    assert time.monotonic() - other < 1.0  # blocked writes would wait ~1.7 s more
    thread.join()
    expect_error(result["r"], 503, "authority_unavailable")
    assert time.monotonic() - started < 3.5  # gave up after about 2 seconds
    assert status(app, slow)["consecutive_failures"] == 1


def test_certificates_survive_restart(app, hub):
    t = setup(app, hub)
    hub.approve(t, "w", 10**9)
    assert witness_write(app, t, "w").status_code == 201
    app.restart()
    hub.fault(f"authority:{t}", fail_next=1, fail_status=500)
    assert witness_write(app, t, "w").status_code == 201
    assert status(app, t)["cached_certificates"] == 1
