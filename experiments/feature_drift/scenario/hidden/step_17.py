from decimal import ROUND_HALF_UP, Decimal

import hub as h
from conftest import calls, error_code, tool_calls

NEW = {"fxrates": "rest", "lingo": "mcp", "shiptrack": "rest"}


def invoke(app, name, payload, ws=None):
    return app.post(f"/api/capabilities/{name}/invoke", ws, json={"input": payload})


def conversion(amount, source, target):
    ratio = Decimal(h.RATES[target]) / Decimal(h.RATES[source])
    return {
        "amount": f"{Decimal(amount):.2f}",
        "from": source,
        "to": target,
        "rate": str(ratio.quantize(Decimal("0.000001"), ROUND_HALF_UP)),
        "converted": str((Decimal(amount) * ratio).quantize(Decimal("0.01"), ROUND_HALF_UP)),
    }


def test_fx_conversion(app, hub):
    app.creds("fxrates")
    response = app.get("/api/fx/convert", params={"amount": "100", "from": "EUR", "to": "NOK"})
    assert response.status_code == 200, response.text
    assert response.json() == conversion("100", "EUR", "NOK")
    output = invoke(app, "fxrates.convert", {"amount": "12.5", "from": "GBP", "to": "JPY"}).json()
    assert output["output"] == conversion("12.5", "GBP", "JPY")
    response = app.get("/api/fx/convert", params={"amount": "5", "from": "EUR", "to": "XXX"})
    assert response.status_code == 422 and error_code(response) == "unsupported_currency"
    for amount in ("-5", "1.234", "abc", "0"):
        response = app.get("/api/fx/convert", params={"amount": amount, "from": "EUR", "to": "USD"})
        assert response.status_code == 422, amount


def test_translation(app, hub):
    app.creds("lingo")
    response = invoke(app, "lingo.translate", {"text": "hello", "target": "es"})
    assert response.status_code == 200, response.text
    assert response.json()["output"] == {
        "text": "hello",
        "target": "es",
        "translation": "hola",
        "source": "en",
    }
    sent = tool_calls(hub, "lingo", "translate")[0]["params"]["arguments"]
    assert sent == {"text": "hello", "target_lang": "es"}
    response = invoke(app, "lingo.translate", {"text": "hello"})
    assert response.status_code == 422


def test_shipments(app, hub):
    app.creds("shiptrack")
    body = app.get("/api/shipments/SH123").json()
    assert body == {
        "code": "SH123",
        "status": "in_transit",
        "delivered": False,
        "last_event": {
            "at": "2026-01-13T14:30:00Z",
            "location": "Gothenburg",
            "description": "In transit",
        },
        "events": [
            {"at": "2026-01-12T08:00:00Z", "location": "Oslo", "description": "Picked up"},
            {"at": "2026-01-13T14:30:00Z", "location": "Gothenburg", "description": "In transit"},
        ],
    }
    output = invoke(app, "shiptrack.track", {"code": "SH456"}).json()["output"]
    assert output["delivered"] is True and output["last_event"]["description"] == "Delivered"
    response = app.get("/api/shipments/NOPE")
    assert response.status_code == 404 and error_code(response) == "not_found"


def test_platform_rules_apply(app, hub):
    ws = app.workspace()
    rows = {r["name"]: r for r in app.get("/api/integrations", ws).json()["integrations"]}
    for name, kind in NEW.items():
        assert rows[name]["kind"] == kind and rows[name]["status"] == "unconfigured"
    caps = {r["name"]: r for r in app.get("/api/capabilities", ws).json()["capabilities"]}
    assert {"fxrates.convert", "lingo.translate", "shiptrack.track"} <= set(caps)
    assert not caps["lingo.translate"]["available"]
    response = invoke(app, "lingo.translate", {"text": "hello", "target": "fr"}, ws)
    assert response.status_code == 409 and error_code(response) == "not_configured"
    app.creds(*NEW, ws=ws)
    rows = {r["name"]: r for r in app.get("/api/integrations", ws).json()["integrations"]}
    assert all(rows[name]["status"] == "ok" for name in NEW)
    app.settings(ws, quotas={"fxrates": 1}, cache_ttl_seconds={"shiptrack": 60})
    before = len(app.audit(ws))
    assert (
        invoke(app, "fxrates.convert", {"amount": "1", "from": "USD", "to": "EUR"}, ws).status_code
        == 200
    )
    response = invoke(app, "fxrates.convert", {"amount": "1", "from": "USD", "to": "EUR"}, ws)
    assert response.status_code == 429 and error_code(response) == "quota_exceeded"
    sent = len(calls(hub, "shiptrack"))
    app.get("/api/shipments/SH123", ws)
    cached = app.get("/api/shipments/SH123", ws)
    assert cached.headers.get("x-cache") == "hit"
    assert len(calls(hub, "shiptrack")) - sent == 1
    entries = app.audit(ws)[: len(app.audit(ws)) - before]
    assert {"fxrates", "shiptrack"} <= {e["integration"] for e in entries}
    usage = app.get("/api/usage", ws).json()["integrations"]
    assert usage["fxrates"] == {"used": 1, "quota": 1} and usage["shiptrack"]["used"] == 1
    app.settings(ws, disabled_integrations=["shiptrack"])
    response = app.get("/api/shipments/SH123", ws)
    assert response.status_code == 403 and error_code(response) == "integration_disabled"
    assert app.delete("/api/credentials/lingo", ws).status_code == 204
    assert app.get("/api/integrations/lingo", ws).json()["status"] == "unconfigured"


def test_bad_credentials(app, hub):
    ws = app.workspace()
    app.put("/api/credentials/shiptrack", ws, json={"username": "ship-user", "password": "nope"})
    response = app.get("/api/shipments/SH123", ws)
    assert response.status_code == 502 and error_code(response) == "upstream_auth"
    app.put("/api/credentials/fxrates", ws, json={"app_id": "nope"})
    response = app.get("/api/fx/convert", ws, params={"amount": "1", "from": "USD", "to": "EUR"})
    assert response.status_code == 502 and error_code(response) == "upstream_auth"
