"""Request 11: tiered fee schedule and clawbacks of retracted grants."""

from conftest import expect_error, uid

DAY = 86400
SCHEDULE = {
    "brackets": [
        {"up_to": "1000.00", "rate": "0.05"},
        {"up_to": "5000.00", "rate": "0.03"},
        {"up_to": None, "rate": "0.01"},
    ]
}


def grant(app, tenant, payee, amount, **kw):
    return app.add("grant:" + uid(), {"payee": payee, "amount": amount}, 100, tenant=tenant, **kw)


def clawback(app, tenant, ident):
    response = app.get(f"/api/clawbacks/{ident}", tenant)
    assert response.status_code == 200, response.text
    return response.json()


def test_schedule(app):
    t = app.tenant()
    expect_error(app.get("/api/fees/schedule", t), 409, "not_configured")
    response = app.put("/api/fees/schedule", t, json=SCHEDULE)
    assert response.status_code == 200, response.text
    assert response.json() == SCHEDULE
    assert app.get("/api/fees/schedule", t).json() == SCHEDULE
    for bad in (
        [],
        [{"up_to": "100", "rate": "0.1"}],
        [{"up_to": None, "rate": "0.1"}, {"up_to": None, "rate": "0.1"}],
        [
            {"up_to": "100", "rate": "0.1"},
            {"up_to": "50", "rate": "0.1"},
            {"up_to": None, "rate": "0"},
        ],
        [{"up_to": None, "rate": "1.5"}],
        [{"up_to": None, "rate": 0.1}],
        [{"up_to": 100, "rate": "0.1"}, {"up_to": None, "rate": "0.1"}],
        [{"up_to": "-5", "rate": "0.1"}, {"up_to": None, "rate": "0.1"}],
    ):
        expect_error(
            app.put("/api/fees/schedule", t, json={"brackets": bad}), 422, "invalid_request"
        )
    assert app.get("/api/fees/schedule", t).json() == SCHEDULE


def test_late_clawback_matches_worked_example(app):
    t = app.tenant()
    app.put("/api/fees/schedule", t, json=SCHEDULE)
    grant(app, t, "studio-a", "3000.00")
    grant(app, t, "studio-a", "2000.00")
    grant(app, t, "studio-b", "999.00")  # another payee
    app.add("payout:x", {"payee": "studio-a", "amount": "50.00"}, 1, tenant=t)  # not a grant key
    g9 = grant(app, t, "studio-a", "800.00")
    at = g9["asserted_at"] + 31 * DAY + 5
    assert app.retract(g9["id"], tenant=t, asserted_at=at).status_code == 200
    app.now = at
    expected = {
        "assertion_id": g9["id"],
        "payee": "studio-a",
        "gross": "800.00",
        "volume_before": "5800.00",
        "volume_after": "5000.00",
        "fee_before": "178.00",
        "fee_after": "170.00",
        "marginal_fee": "8.00",
        "days_elapsed": 31,
        "late": True,
        "payee_clawback": "792.00",
        "fee_refund": "0.00",
        "net_adjustment": "792.00",
    }
    assert clawback(app, t, g9["id"]) == expected
    grant(app, t, "studio-a", "100.00")  # later history does not change the answer
    assert clawback(app, t, g9["id"]) == expected


def test_timely_clawback_rounding_and_volume(app):
    t = app.tenant()
    app.put(
        "/api/fees/schedule",
        t,
        json={"brackets": [{"up_to": "100.00", "rate": "0.035"}, {"up_to": None, "rate": "0.01"}]},
    )
    grant(app, t, "p", "10.10")
    old = grant(app, t, "p", "70.00")
    assert app.retract(old["id"], tenant=t).status_code == 200  # retracted earlier: not counted
    app.add("grant:" + uid(), {"payee": "p", "amount": "1.234"}, 1, tenant=t)  # malformed: ignored
    target = grant(app, t, "p", "5.00")
    assert app.retract(target["id"], tenant=t).status_code == 200
    grant(app, t, "p", "500.00")  # recorded after the retraction: not counted
    body = clawback(app, t, target["id"])
    assert body == {
        "assertion_id": target["id"],
        "payee": "p",
        "gross": "5.00",
        "volume_before": "15.10",
        "volume_after": "10.10",
        "fee_before": "0.53",
        "fee_after": "0.35",
        "marginal_fee": "0.18",
        "days_elapsed": 0,
        "late": False,
        "payee_clawback": "4.82",
        "fee_refund": "0.18",
        "net_adjustment": "5.00",
    }


def test_thirty_day_boundary(app):
    t = app.tenant()
    app.put("/api/fees/schedule", t, json={"brackets": [{"up_to": None, "rate": "0.10"}]})
    first = grant(app, t, "q", "100.00")
    second = grant(app, t, "q", "100.00")
    start = second["asserted_at"]
    app.retract(first["id"], tenant=t, asserted_at=first["asserted_at"] + 30 * DAY - 1)
    app.retract(second["id"], tenant=t, asserted_at=start + 30 * DAY)
    app.now = start + 30 * DAY
    a, b = clawback(app, t, first["id"]), clawback(app, t, second["id"])
    assert (a["days_elapsed"], a["late"], a["fee_refund"]) == (29, False, "10.00")
    assert (b["days_elapsed"], b["late"], b["fee_refund"]) == (30, True, "0.00")
    assert b["volume_before"] == "100.00" and b["net_adjustment"] == "90.00"


def test_errors(app):
    t = app.tenant()
    live = grant(app, t, "p", "1.00")
    other = app.add("note", {"payee": "p", "amount": "1.00"}, 1, tenant=t)
    broken = app.add("grant:" + uid(), {"payee": "", "amount": "1.00"}, 1, tenant=t)
    gone = grant(app, t, "p", "2.00")
    for ident in (other["id"], broken["id"], gone["id"]):
        assert app.retract(ident, tenant=t).status_code == 200
    expect_error(app.get(f"/api/clawbacks/{gone['id']}", t), 409, "not_configured")
    app.put("/api/fees/schedule", t, json=SCHEDULE)
    expect_error(app.get(f"/api/clawbacks/{uid('none-')}", t), 404, "not_found")
    expect_error(app.get(f"/api/clawbacks/{live['id']}", t), 409, "not_retracted")
    expect_error(app.get(f"/api/clawbacks/{other['id']}", t), 422, "not_a_grant")
    expect_error(app.get(f"/api/clawbacks/{broken['id']}", t), 422, "not_a_grant")
    assert clawback(app, t, gone["id"])["volume_before"] == "3.00"
