import math
import time
from decimal import Decimal

import hub as h
from conftest import calls, error_code, fault


def expected(state, dataset="a"):
    rows = [r for r in h.invoices(dataset) if state == "all" or r["state"] == state]
    out = [
        {
            "id": r["invoice_id"],
            "customer": r["customer_name"],
            "amount": f"{Decimal(r['amount_cents']) / 100:.2f}",
            "currency": r["currency"],
            "due_date": r["due"],
            "status": r["state"],
        }
        for r in rows
    ]
    out.sort(key=lambda r: (r["due_date"], r["id"]))
    totals = {}
    for row in out:
        totals[row["currency"]] = totals.get(row["currency"], Decimal(0)) + Decimal(row["amount"])
    return {"invoices": out, "totals": {k: f"{v:.2f}" for k, v in totals.items()}}


def pages(state):
    return max(1, math.ceil(sum(state in ("all", r["state"]) for r in h.invoices("a")) / 10))


def test_open_invoices(app, hub):
    app.creds("ledger")
    response = app.get("/api/invoices")
    assert response.status_code == 200, response.text
    assert response.json() == expected("open")
    assert all(c["headers"].get("x-ledger-key") == "ledger-key-a" for c in calls(hub, "ledger"))


def test_other_states(app, hub):
    app.creds("ledger")
    assert app.get("/api/invoices", params={"status": "paid"}).json() == expected("paid")
    assert app.get("/api/invoices", params={"status": "all"}).json() == expected("all")
    response = app.get("/api/invoices", params={"status": "late"})
    assert response.status_code == 422 and error_code(response) == "invalid_request"


def test_rate_limits_are_retried(app, hub):
    app.creds("ledger")
    fault(hub, "ledger", rate_limit_next=2, retry_after=0)
    response = app.get("/api/invoices")
    assert response.status_code == 200 and response.json() == expected("open")
    assert len(calls(hub, "ledger")) == 2 + pages("open")


def test_retry_after_is_respected(app, hub):
    app.creds("ledger")
    fault(hub, "ledger", rate_limit_next=1, retry_after=1)
    started = time.monotonic()
    assert app.get("/api/invoices").status_code == 200
    assert time.monotonic() - started >= 0.9


def test_retries_are_bounded(app, hub):
    app.creds("ledger")
    fault(hub, "ledger", rate_limit_next=50, retry_after=0)
    response = app.get("/api/invoices")
    assert response.status_code == 503 and error_code(response) == "upstream_rate_limited"
    assert len(calls(hub, "ledger")) == 4


def test_bad_account_and_overview(fresh_app, hub):
    response = fresh_app.get("/api/invoices")
    assert response.status_code == 409 and error_code(response) == "not_configured"
    fresh_app.put(
        "/api/credentials/ledger", json={"api_key": "ledger-key-a", "account_id": "acc-999"}
    )
    response = fresh_app.get("/api/invoices")
    assert response.status_code == 502 and error_code(response) == "upstream_auth"
    fresh_app.creds("ledger")
    row = fresh_app.get("/api/integrations/ledger").json()
    assert row["kind"] == "rest" and row["status"] == "ok"
