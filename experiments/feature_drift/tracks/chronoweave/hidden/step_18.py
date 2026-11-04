"""Request 18: role-scoped redaction views with blinded commitments."""

import hashlib
import json

from conftest import canonical, expect_error, sha256, uid


def secret_history(app, t):
    tag = uid("SECRET")
    a = app.add(
        f"{tag}-key-a",
        f"{tag}-value-a",
        10,
        tenant=t,
        id=f"{tag}-a",
        author=f"{tag}-ann",
        node=f"{tag}-n1",
    )
    b = app.add(
        f"{tag}-key-b",
        {"v": f"{tag}-v"},
        20,
        tenant=t,
        id=f"{tag}-b",
        parents=[a["id"]],
        author=f"{tag}-bob",
    )
    c = app.add(f"{tag}-key-c", 3, 30, tenant=t, id=f"{tag}-c", parents=[a["id"], b["id"]])
    return tag, a, b, c


def graph(app, t, **params):
    response = app.call("GET", "/api/audit/graph", t, role="auditor", params=params)
    assert response.status_code == 200, response.text
    return response


def opening(app, t, ident):
    response = app.get(f"/api/branches/main/assertions/{ident}/opening", t)
    assert response.status_code == 200, response.text
    return response.json()


def test_roles(app):
    t = app.tenant()
    tag, a, *_ = secret_history(app, t)
    expect_error(
        app.call("GET", "/api/branches/main/state", t, role="admin"), 422, "invalid_request"
    )
    assert app.call("GET", "/api/health", role="auditor").status_code == 200
    for method, path, body in (
        ("GET", "/api/branches/main/state", None),
        ("GET", f"/api/branches/main/assertions/{a['id']}", None),
        ("GET", "/api/branches/main/assertions", None),
        ("GET", f"/api/branches/main/assertions/{a['id']}/opening", None),
        ("POST", "/api/query", {"q": ""}),
        ("GET", "/api/bundles/export", None),
        ("GET", "/api/branches", None),
        ("POST", "/api/branches/main/assertions", {"id": "x"}),
        ("GET", "/api/tenants", None),
    ):
        response = app.call(method, path, t, role="auditor", json=body)
        expect_error(response, 403, "payload_clearance_required")
        assert tag not in response.text
    assert app.call("GET", "/api/branches/main/state", t, role="owner").status_code == 200


def test_redacted_graph_hides_payloads(app):
    t = app.tenant()
    tag, a, b, c = secret_history(app, t)
    t_mid = app.now
    assert app.retract(b["id"], tenant=t).status_code == 200
    response = graph(app, t)
    assert tag not in response.text
    body = response.json()
    assert body["asserted_at"] is None
    opened = {k: opening(app, t, x["id"])["commitment"] for k, x in (("a", a), ("b", b), ("c", c))}
    expected_nodes = [
        {
            "commitment": opened["a"],
            "effective_at": 10,
            "asserted_at": a["asserted_at"],
            "retracted_at": None,
            "parents": [],
        },
        {
            "commitment": opened["b"],
            "effective_at": 20,
            "asserted_at": b["asserted_at"],
            "retracted_at": app.record(b["id"], tenant=t)["retracted_at"],
            "parents": [opened["a"]],
        },
        {
            "commitment": opened["c"],
            "effective_at": 30,
            "asserted_at": c["asserted_at"],
            "retracted_at": None,
            "parents": sorted([opened["a"], opened["b"]]),
        },
    ]
    assert body["nodes"] == expected_nodes
    assert body["root"] == sha256(expected_nodes)
    old = graph(app, t, asserted_at=b["asserted_at"]).json()
    assert old["asserted_at"] == b["asserted_at"]
    assert [n["commitment"] for n in old["nodes"]] == [opened["a"], opened["b"]]
    assert old["nodes"][1]["retracted_at"] is None
    assert graph(app, t, asserted_at=t_mid).json()["nodes"][1]["retracted_at"] is None
    expect_error(
        app.call("GET", "/api/audit/graph", uid("t-"), role="auditor"), 404, "unknown_tenant"
    )


def test_openings_and_commitments(app):
    t = app.tenant()
    _, a, *_ = secret_history(app, t)
    first = opening(app, t, a["id"])
    assert first == opening(app, t, a["id"])
    assert len(first["salt"]) == 32 and int(first["salt"], 16) >= 0
    payload = {k: a[k] for k in ("id", "key", "value", "author", "node")}
    assert first["payload"] == payload
    data = first["salt"].encode() + b"\n" + canonical(payload)
    assert first["commitment"] == hashlib.sha256(data).hexdigest()
    # salts are per assertion
    other = app.add("k", a["value"], 10, tenant=t)
    assert opening(app, t, other["id"])["salt"] != first["salt"]
    # promoted assertions get commitments when they land on main
    name = app.fork(tenant=t)
    hyp = app.add("hyp", 1, 5, branch=name, tenant=t)
    expect_error(app.get(f"/api/branches/main/assertions/{hyp['id']}/opening", t), 404, "not_found")
    assert app.merge(name, tenant=t).status_code == 200
    promoted = opening(app, t, hyp["id"])
    assert promoted["commitment"] in [n["commitment"] for n in graph(app, t).json()["nodes"]]
    expect_error(
        app.get(f"/api/branches/main/assertions/{uid('no-')}/opening", t), 404, "not_found"
    )


def test_verify_checks_the_submitted_graph(app):
    t = app.tenant()
    secret_history(app, t)
    good = graph(app, t).json()
    response = app.call("POST", "/api/audit/verify", t, role="auditor", json=good)
    assert response.status_code == 200, response.text
    assert response.json() == {"valid": True, "violations": []}
    nodes = json.loads(json.dumps(good["nodes"]))
    a, b, c = nodes
    c["asserted_at"] = a["asserted_at"] - 1  # earlier than its parents
    b["parents"].append("f" * 64)  # dangling
    a["retracted_at"] = a["asserted_at"] - 5
    bad = {"nodes": nodes, "root": good["root"]}
    body = app.call("POST", "/api/audit/verify", t, role="auditor", json=bad).json()
    expected = sorted(
        [
            {"commitment": c["commitment"], "problem": "timestamp_regression"},
            {"commitment": b["commitment"], "problem": "dangling_parent"},
            {"commitment": a["commitment"], "problem": "retraction_before_assertion"},
        ],
        key=lambda v: (v["commitment"], v["problem"]),
    )
    assert body == {
        "valid": False,
        "violations": [{"commitment": None, "problem": "root_mismatch"}, *expected],
    }
    resealed = {"nodes": good["nodes"], "root": "0" * 64}
    assert app.call("POST", "/api/audit/verify", t, role="auditor", json=resealed).json()[
        "violations"
    ] == [{"commitment": None, "problem": "root_mismatch"}]
    for junk in (
        {"nodes": "x", "root": "y"},
        {"root": "y"},
        [],
        {"nodes": [{"commitment": 1}], "root": "y"},
    ):
        expect_error(
            app.call("POST", "/api/audit/verify", t, role="auditor", json=junk),
            422,
            "invalid_request",
        )
