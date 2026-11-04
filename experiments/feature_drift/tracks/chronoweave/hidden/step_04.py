"""Request 4: derived grants and compensation obligations from non-monotonic rules."""

from conftest import expect_error, uid


def make_rule(app, p, capability="approve", requires=None, unless=None, ident=None):
    body = {
        "id": ident or uid("r-"),
        "capability": capability,
        "requires": requires or [{"key": p + "role", "equals": "approver"}],
        "unless": unless if unless is not None else [{"key": p + "suspended", "equals": True}],
    }
    response = app.post("/api/rules", json=body)
    assert response.status_code == 201, response.text
    assert response.json() == body
    return body


def obligations(app, rule_id):
    response = app.get("/api/obligations", params={"rule": rule_id})
    assert response.status_code == 200, response.text
    return response.json()["obligations"]


def strip(rows):
    return [{k: v for k, v in row.items() if k != "id"} for row in rows]


def grants(app, **params):
    return [g for g in app.get("/api/grants", params=params).json()["grants"]]


def test_rule_validation_and_listing(app):
    p = uid("k") + ":"
    rule = make_rule(app, p)
    no_unless = {"id": uid("r-"), "capability": "c", "requires": [{"key": p + "x", "equals": None}]}
    response = app.post("/api/rules", json=no_unless)
    assert response.status_code == 201 and response.json()["unless"] == []
    expect_error(app.post("/api/rules", json=rule), 409, "conflict")
    for bad in (
        {**rule, "id": uid("r-"), "requires": []},
        {**rule, "id": uid("r-"), "requires": [{"key": p + "x"}]},
        {**rule, "id": uid("r-"), "requires": [{"key": "", "equals": 1}]},
        {**rule, "id": uid("r-"), "capability": ""},
        {**rule, "id": uid("r-"), "unless": "nope"},
        {**rule, "id": "-bad"},
    ):
        expect_error(app.post("/api/rules", json=bad), 422, "invalid_request")
    listed = app.get("/api/rules").json()["rules"]
    assert rule in listed and [r["id"] for r in listed] == sorted(r["id"] for r in listed)


def test_grants_follow_bitemporal_state(app):
    p = uid("k") + ":"
    rule = make_rule(app, p)
    mine = lambda **kw: [g for g in grants(app, **kw) if g["rule"] == rule["id"]]  # noqa: E731
    app.add(p + "role", "approver", 100)
    t_granted = app.now
    assert mine(effective_at=150) == [{"rule": rule["id"], "capability": "approve"}]
    assert mine(effective_at=50) == []
    app.add(p + "suspended", True, 200)
    assert mine(effective_at=250) == []
    assert mine(effective_at=150) == [{"rule": rule["id"], "capability": "approve"}]
    assert mine(effective_at=250, asserted_at=t_granted) == [
        {"rule": rule["id"], "capability": "approve"}
    ]
    assert mine() == []
    app.add(p + "suspended", False, 300)
    assert mine() == [{"rule": rule["id"], "capability": "approve"}]


def test_late_negative_assertion_emits_obligation(app):
    p = uid("k") + ":"
    rule = make_rule(app, p, capability="pay")
    app.add(p + "role", "approver", 1000)
    app.add(p + "suspended", False, 1800)
    assert obligations(app, rule["id"]) == []
    late = app.add(p + "suspended", True, 1500)  # back-dated, arrives later
    rows = obligations(app, rule["id"])
    assert strip(rows) == [
        {
            "rule": rule["id"],
            "capability": "pay",
            "effective_from": 1500,
            "effective_to": 1800,
            "asserted_at": late["asserted_at"],
            "cause": late["id"],
        }
    ]
    assert rows[0]["id"].startswith("obl-")
    # open-ended revocation
    end = app.add(p + "role", "viewer", 2000)
    rows = obligations(app, rule["id"])
    assert strip(rows[1:]) == [
        {
            "rule": rule["id"],
            "capability": "pay",
            "effective_from": 2000,
            "effective_to": None,
            "asserted_at": end["asserted_at"],
            "cause": end["id"],
        }
    ]
    numbers = [int(r["id"].split("-")[1]) for r in rows]
    assert numbers == sorted(numbers) and len(set(numbers)) == 2


def test_retractions_and_merges(app):
    p = uid("k") + ":"
    rule = make_rule(app, p, unless=[])
    r1 = app.add(p + "role", "approver", 100)
    r2 = app.add(p + "role", "viewer", 500)
    t = app.t()
    assert app.retract(r1["id"], asserted_at=t).status_code == 200
    assert strip(obligations(app, rule["id"])) == [
        {
            "rule": rule["id"],
            "capability": "approve",
            "effective_from": 500,
            "effective_to": None,
            "asserted_at": r2["asserted_at"],
            "cause": r2["id"],
        },
        {
            "rule": rule["id"],
            "capability": "approve",
            "effective_from": 100,
            "effective_to": 500,
            "asserted_at": t,
            "cause": r1["id"],
        },
    ]
    assert app.retract(r2["id"]).status_code == 200  # only removes a revocation: nothing new
    assert len(obligations(app, rule["id"])) == 2
    app.add(p + "role", "approver", 700)
    name = app.fork()
    app.add(p + "role", "auditor", 900, branch=name)
    t_merge = app.t()
    assert app.merge(name, asserted_at=t_merge).status_code == 200
    rows = obligations(app, rule["id"])
    assert strip(rows[2:]) == [
        {
            "rule": rule["id"],
            "capability": "approve",
            "effective_from": 900,
            "effective_to": None,
            "asserted_at": t_merge,
            "cause": f"merge:{name}",
        }
    ]


def test_one_write_many_rules_and_no_backfill(app):
    p = uid("k") + ":"
    first = make_rule(app, p, ident="a" + uid("r"), capability="one")
    second = make_rule(app, p, ident="b" + uid("r"), capability="two")
    app.add(p + "role", "approver", 10)
    app.add(p + "unrelated", 1, 10)
    assert obligations(app, first["id"]) == [] and obligations(app, second["id"]) == []
    hit = app.add(p + "suspended", True, 40)
    all_rows = app.get("/api/obligations").json()["obligations"]
    mine = [r for r in all_rows if r["cause"] == hit["id"]]
    assert [(r["rule"], r["effective_from"], r["effective_to"]) for r in mine] == [
        (first["id"], 40, None),
        (second["id"], 40, None),
    ]
    numbers = [int(r["id"].split("-")[1]) for r in mine]
    assert numbers[1] == numbers[0] + 1
    later = make_rule(app, p, ident="c" + uid("r"), unless=[{"key": p + "other", "equals": 1}])
    assert obligations(app, later["id"]) == []
