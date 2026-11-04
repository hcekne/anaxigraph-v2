"""Request 10: portable gzip audit bundles, verification and import."""

import gzip
import json

from conftest import canonical, expect_error, sha256

FIELDS = (
    "id",
    "key",
    "value",
    "effective_at",
    "asserted_at",
    "causal_parents",
    "author",
    "node",
    "retracted_at",
)


def history(app, tenant):
    """A small history with out-of-slice ancestors and retractions on both sides of a cut."""
    root = app.add("acct", {"open": True}, 5, tenant=tenant, node="n1")
    a = app.add("limit", 100, 150, parents=[root["id"]], tenant=tenant)
    b = app.add("limit", 200, 180, parents=[a["id"]], tenant=tenant, author="bob")
    gone = app.add("note", "draft", 160, tenant=tenant)
    assert app.retract(gone["id"], tenant=tenant).status_code == 200
    cut = app.now
    late = app.add("owner", "ann", 170, tenant=tenant)
    assert app.retract(b["id"], tenant=tenant).status_code == 200  # after the cut
    app.add("outside", 1, 900, tenant=tenant)
    return {"root": root, "a": a, "b": b, "gone": gone, "late": late, "cut": cut}


def export(app, tenant, **params):
    response = app.get("/api/bundles/export", tenant, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/gzip")
    return response.content


def pack(document):
    return gzip.compress(canonical(document), mtime=0)


def test_export_slice(app):
    t = app.tenant()
    h = history(app, t)
    data = export(app, t, effective_from=100, effective_to=200, asserted_at=h["cut"])
    assert data == export(app, t, effective_from=100, effective_to=200, asserted_at=h["cut"])
    document = json.loads(gzip.decompress(data))
    assert data == gzip.compress(canonical(document), mtime=0)
    assert document["format"] == "chronoweave-bundle/1"
    assert document["slice"] == {
        "effective_from": 100,
        "effective_to": 200,
        "asserted_at": h["cut"],
    }
    rows = document["assertions"]
    assert [r["id"] for r in rows] == [h["root"]["id"], h["a"]["id"], h["gone"]["id"], h["b"]["id"]]
    assert all(set(r) == set(FIELDS) for r in rows)
    by_id = {r["id"]: r for r in rows}
    assert by_id[h["b"]["id"]]["retracted_at"] is None  # retracted only after the cut
    assert by_id[h["gone"]["id"]]["retracted_at"] is not None
    assert by_id[h["root"]["id"]]["value"] == {"open": True}
    assert by_id[h["b"]["id"]]["author"] == "bob" and by_id[h["root"]["id"]]["node"] == "n1"
    assert document["digest"] == sha256(rows)
    full = json.loads(gzip.decompress(export(app, t)))
    assert full["slice"] == {"effective_from": None, "effective_to": None, "asserted_at": None}
    assert len(full["assertions"]) == 6
    assert {r["id"]: r for r in full["assertions"]}[h["b"]["id"]]["retracted_at"] is not None


def test_verify_checks_only_the_file(app):
    t = app.tenant()
    history(app, t)
    data = export(app, t)
    document = json.loads(gzip.decompress(data))
    response = app.post("/api/bundles/verify", t, content=data)
    assert response.status_code == 200, response.text
    assert response.json() == {"valid": True, "assertions": 6, "digest": document["digest"]}
    expect_error(app.post("/api/bundles/verify", t, content=b"not gzip"), 422, "invalid_bundle")
    expect_error(
        app.post("/api/bundles/verify", t, content=gzip.compress(b"{nope")), 422, "invalid_bundle"
    )
    expect_error(
        app.post("/api/bundles/verify", t, content=pack({**document, "format": "x"})),
        422,
        "invalid_bundle",
    )
    no_id = json.loads(json.dumps(document))
    del no_id["assertions"][0]["id"]
    expect_error(app.post("/api/bundles/verify", t, content=pack(no_id)), 422, "invalid_bundle")
    tampered = json.loads(json.dumps(document))
    tampered["assertions"][1]["value"] = 999999
    expect_error(app.post("/api/bundles/verify", t, content=pack(tampered)), 422, "digest_mismatch")

    def resealed(mutate):
        copy = json.loads(json.dumps(document))
        mutate(copy["assertions"])
        copy["digest"] = sha256(copy["assertions"])
        return pack(copy)

    child = next(i for i, r in enumerate(document["assertions"]) if r["causal_parents"])
    for mutate in (
        lambda rows: rows[child]["causal_parents"].append("missing-parent"),
        lambda rows: rows[child].update(asserted_at=0),
        lambda rows: rows[0].update(retracted_at=rows[0]["asserted_at"] - 1),
        lambda rows: rows.append(dict(rows[0])),
    ):
        expect_error(
            app.post("/api/bundles/verify", t, content=resealed(mutate)), 422, "invalid_bundle"
        )


def test_import_replays_identically(app):
    source = app.tenant()
    h = history(app, source)
    rule = {
        "id": "r",
        "capability": "c",
        "requires": [{"key": "limit", "equals": 100}],
        "unless": [],
    }
    data = export(app, source)
    target = app.tenant()
    assert app.post("/api/rules", target, json=rule).status_code == 201
    app.put(
        "/api/verifiers/escrow_hold",
        target,
        json={"enabled": True, "config": {"holds": [{"key": "limit", "until": 10**9}]}},
    )
    response = app.post("/api/bundles/import", target, content=data)
    assert response.status_code == 200, response.text
    assert response.json() == {"imported": 6, "digest": json.loads(gzip.decompress(data))["digest"]}
    coordinates = [(None, None), (170, h["cut"]), (200, None), (None, h["a"]["asserted_at"])]
    for effective, known in coordinates:
        assert app.state(tenant=target, effective_at=effective, asserted_at=known) == app.state(
            tenant=source, effective_at=effective, asserted_at=known
        )
    for key in ("a", "b", "late"):
        ident = h[key]["id"]
        assert app.record(ident, tenant=target) == app.record(ident, tenant=source)
    assert export(app, target) == data
    assert app.get("/api/obligations", target).json()["obligations"] == []
    expect_error(app.post("/api/bundles/import", target, content=data), 409, "ledger_not_empty")
    clock = max(
        r["retracted_at"] or r["asserted_at"]
        for r in json.loads(gzip.decompress(data))["assertions"]
    )
    expect_error(
        app.write("k", 1, 1, tenant=target, asserted_at=clock - 1), 409, "clock_regression"
    )


def test_import_preconditions(app):
    data = export(app, (lambda t: (history(app, t), t)[1])(app.tenant()))
    sealed = app.tenant()
    app.seal(0, 1, tenant=sealed)
    expect_error(app.post("/api/bundles/import", sealed, content=data), 409, "ledger_not_empty")
    tiny = app.tenant(max_assertion_bytes=10)
    expect_error(app.post("/api/bundles/import", tiny, content=data), 409, "budget_exceeded")
    assert app.state(tenant=tiny) == {}
    expect_error(
        app.post("/api/bundles/import", app.tenant(), content=b"\x1f\x8b junk"),
        422,
        "invalid_bundle",
    )


def test_cross_node_import(app, fresh_app):
    t = app.tenant()
    h = history(app, t)
    data = export(app, t, effective_from=100, effective_to=200)
    response = fresh_app.post("/api/bundles/import", content=data)
    assert response.status_code == 200, response.text
    for known in (h["cut"], None):
        expected = {
            k: v for k, v in app.state(tenant=t, asserted_at=known).items() if k != "outside"
        }
        assert fresh_app.state(asserted_at=known) == expected
    assert fresh_app.record(h["b"]["id"])["status"] == "retracted"
