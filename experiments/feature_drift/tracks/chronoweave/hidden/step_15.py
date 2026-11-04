"""Request 15: the lineage query language."""

from conftest import expect_error


def q(app, tenant, text, branch=None):
    body = {"q": text}
    if branch:
        body["branch"] = branch
    response = app.post("/api/query", tenant, json=body)
    assert response.status_code == 200, response.text
    return [r["id"] for r in response.json()["assertions"]]


def build(app, t):
    ids = {}
    ids["root"] = app.add("acct:1", 1, 10, tenant=t, author="ann", node="n1", id="root")["id"]
    ids["b1"] = app.add(
        "acct:2", 2, 20, tenant=t, author="bob", node="n2", parents=["root"], id="b1"
    )["id"]
    ids["b2"] = app.add(
        "limit:1", 3, 30, tenant=t, author="ann", node="n2", parents=["root"], id="b2"
    )["id"]
    ids["d"] = app.add(
        "limit:2", 4, 40, tenant=t, author="cy", node="n1", parents=["b1", "b2"], id="d"
    )["id"]
    ids["other"] = app.add("misc", 5, 50, tenant=t, author="bob", node="n3", id="other")["id"]
    ids["side"] = app.add(
        "misc:2", 6, 60, tenant=t, author="dee", node="n3", parents=["other"], id="side"
    )["id"]
    return ids


def test_field_predicates_and_boolean_logic(app):
    t = app.tenant()
    build(app, t)
    assert q(app, t, "") == ["root", "b1", "b2", "d", "other", "side"]
    assert q(app, t, 'WHERE KEY = "acct:1"') == ["root"]
    assert q(app, t, 'where key prefix "acct:"') == ["root", "b1"]
    assert q(app, t, 'WHERE AUTHOR = "ann"') == ["root", "b2"]
    assert q(app, t, 'WHERE NODE = "n2" AND NOT AUTHOR = "bob"') == ["b2"]
    assert q(app, t, 'WHERE AUTHOR = "bob" OR AUTHOR = "cy" AND NODE = "n3"') == ["b1", "other"]
    assert q(app, t, 'WHERE (AUTHOR = "bob" OR AUTHOR = "cy") AND NODE = "n1"') == ["d"]
    assert q(app, t, 'WHERE NOT NOT KEY PREFIX "misc"') == ["other", "side"]
    assert q(app, t, "EFFECTIVE BETWEEN 20 AND 40") == ["b1", "b2", "d"]
    assert q(app, t, 'EFFECTIVE BETWEEN 20 AND 40 WHERE AUTHOR = "ann"') == ["b2"]
    assert q(app, t, 'WHERE DESCENDS FROM "root"') == ["b1", "b2", "d"]
    assert q(app, t, 'WHERE DESCENDS FROM "nope"') == []
    response = app.post("/api/query", t, json={"q": 'WHERE KEY = "acct:1"'})
    record = response.json()["assertions"][0]
    assert record["key"] == "acct:1" and record["status"] == "active" and record["tainted_by"] == []


def test_taint_status_and_as_of(app):
    t = app.tenant()
    build(app, t)
    before = app.now
    assert app.retract("b1", tenant=t).status_code == 200
    assert app.retract("other", tenant=t).status_code == 200
    assert q(app, t, "WHERE TAINTED") == ["d", "side"]
    assert q(app, t, "WHERE TAINTED BY author:bob") == ["d", "side"]
    assert q(app, t, 'where tainted by author:bob and node = "n1"') == ["d"]
    assert q(app, t, "WHERE TAINTED BY author:ann") == []
    assert q(app, t, "WHERE STATUS = retracted") == ["b1", "other"]
    assert q(app, t, "WHERE STATUS = active") == ["root", "b2"]
    assert q(app, t, f"AS OF {before} WHERE TAINTED") == []
    assert q(app, t, f"as of {before} where status = active") == [
        "root",
        "b1",
        "b2",
        "d",
        "other",
        "side",
    ]
    first = app.record("b1", tenant=t)["asserted_at"]
    assert q(app, t, f"AS OF {first}") == ["root", "b1"]
    response = app.post("/api/query", t, json={"q": f'AS OF {before} WHERE KEY = "acct:2"'})
    assert response.json()["assertions"][0]["retracted_at"] is None


def test_branches(app):
    t = app.tenant()
    build(app, t)
    name = app.fork(tenant=t)
    app.add("acct:9", 9, 90, tenant=t, branch=name, parents=["d"], id="hyp")
    assert q(app, t, 'WHERE DESCENDS FROM "b2"', branch=name) == ["d", "hyp"]
    assert q(app, t, 'WHERE DESCENDS FROM "b2"') == ["d"]
    expect_error(app.post("/api/query", t, json={"q": "", "branch": "no-such"}), 404, "not_found")


def test_syntax_errors_and_hostile_input(app):
    t = app.tenant()
    build(app, t)
    for bad in (
        "WHERE",
        "WHERE KEY",
        'WHERE KEY == "x"',
        "WHERE KEY = x",
        'WHERE KEY = "unterminated',
        'WHERE (KEY = "a"',
        "EFFECTIVE BETWEEN 1",
        "EFFECTIVE BETWEEN -1 AND 5",
        "AS OF x",
        "WHERE STATUS = deleted",
        "WHERE TAINTED BY bob",
        "WHERE TAINTED BY author:",
        "__import__('os').system('true')",
        'WHERE KEY = "a" ; DROP TABLE assertions',
        'WHERE KEY = "a" WHERE',
        "SELECT * FROM assertions",
    ):
        expect_error(app.post("/api/query", t, json={"q": bad}), 422, "query_syntax")
    response = app.post("/api/query", t, json={"q": "WHERE KEY PREFIX 42"})
    expect_error(response, 422, "query_syntax")
    assert "17" in response.json()["error"]["message"]
    expect_error(
        app.post("/api/query", t, json={"q": "WHERE " + "NOT " * 600 + 'KEY = "a"'}),
        422,
        "query_syntax",
    )
    expect_error(app.post("/api/query", t, json={}), 422, "invalid_request")
    expect_error(app.post("/api/query", t, json={"q": 5}), 422, "invalid_request")
    assert q(app, t, r'WHERE KEY = "x\" OR 1=1 --"') == []
    assert q(app, t, 'WHERE KEY = "\'; DROP TABLE assertions; --"') == []
    assert q(app, t, 'WHERE KEY = "acct:1"') == ["root"]
    assert len(app.get("/api/branches/main/assertions", t).json()["assertions"]) == 6
