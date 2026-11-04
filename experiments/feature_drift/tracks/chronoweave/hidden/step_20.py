"""Request 20 (fault probe P3): diamonds across a compacted horizon, retraction races, cycles."""

import gzip
import json
import time

from conftest import canonical, expect_error, sha256, uid, wait_for


def lineage(app, t, ident, branch="main", **params):
    response = app.get(f"/api/branches/{branch}/assertions/{ident}/lineage", t, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_self_reference_is_a_cycle(app):
    t = app.tenant()
    app.add("base", 1, 1, tenant=t, id="base")
    expect_error(
        app.write("k", 1, 1, tenant=t, id="loop", parents=["base", "loop"]), 422, "causal_cycle"
    )
    expect_error(app.get("/api/branches/main/assertions/loop", t), 404, "not_found")
    name = app.fork(tenant=t)
    expect_error(
        app.write("k", 1, 1, tenant=t, id="loop", parents=["loop"], branch=name),
        422,
        "causal_cycle",
    )
    body = {
        "id": "loop",
        "key": "k",
        "value": 1,
        "effective_at": 1,
        "asserted_at": app.t(),
        "causal_parents": ["loop"],
        "author": "x",
    }
    expect_error(app.post("/api/simulate", t, json={"assertions": [body]}), 422, "causal_cycle")


def test_cyclic_bundles_are_rejected(app):
    source = app.tenant()
    app.add("a", 1, 1, tenant=source, id="a")
    app.add("b", 1, 2, tenant=source, id="b", parents=["a"])
    app.add("c", 1, 3, tenant=source, id="c", parents=["b"])
    document = json.loads(gzip.decompress(app.get("/api/bundles/export", source).content))

    def crafted(mutate):
        copy = json.loads(json.dumps(document))
        rows = {r["id"]: r for r in copy["assertions"]}
        mutate(rows)
        copy["digest"] = sha256(copy["assertions"])
        return gzip.compress(canonical(copy), mtime=0)

    def two_cycle(rows):  # a <-> b, same transaction time
        rows["a"]["causal_parents"] = ["b"]
        rows["a"]["asserted_at"] = rows["b"]["asserted_at"]

    def three_cycle(rows):  # a -> c -> b -> a (time order violated too: the cycle wins)
        rows["a"]["causal_parents"] = ["c"]

    def cycle_and_missing(rows):
        two_cycle(rows)
        rows["c"]["causal_parents"] = ["b", "missing"]

    target = app.tenant()
    for mutate in (two_cycle, three_cycle, cycle_and_missing):
        data = crafted(mutate)
        started = time.monotonic()
        expect_error(app.post("/api/bundles/verify", target, content=data), 422, "causal_cycle")
        expect_error(app.post("/api/bundles/import", target, content=data), 422, "causal_cycle")
        assert time.monotonic() - started < 5
    assert app.get("/api/branches/main/assertions", target).json()["assertions"] == []
    tampered = json.loads(json.dumps(document))
    tampered["assertions"][0]["causal_parents"] = ["c"]  # digest not recomputed
    data = gzip.compress(canonical(tampered), mtime=0)
    expect_error(app.post("/api/bundles/verify", target, content=data), 422, "digest_mismatch")
    response = app.post(
        "/api/bundles/import", target, content=app.get("/api/bundles/export", source).content
    )
    assert response.status_code == 200, response.text


def diamond(app, hub):
    """Z (sealed, compacted) <- A <- {B1 via branch 1, B2 via branch 2} <- D (a sealed grant)."""
    t = app.tenant()
    sink = uid("s")
    assert (
        app.post(
            "/api/sinks", t, json={"id": sink, "url": f"{hub.url}/sinks/{sink}", "secret": "x"}
        ).status_code
        == 201
    )
    z = app.add("z", "root", 10, tenant=t, id="z")
    a = app.add("a", "shared", 150, tenant=t, id="a", parents=["z"])
    b1_branch, b2_branch, b3_branch = (app.fork(tenant=t) for _ in range(3))
    app.add("b1", 1, 160, tenant=t, id="b1", parents=["a"], branch=b1_branch)
    app.add("b2", 2, 170, tenant=t, id="b2", parents=["a"], branch=b2_branch)
    app.add("b3", 3, 175, tenant=t, id="b3", parents=["a"], branch=b3_branch)
    app.seal(0, 99, ident="q1", tenant=t)
    summary = app.post(
        "/api/compactions", t, json={"watermark": 100, "asserted_at": app.t()}
    ).json()["compactions"]
    assert [s["id"] for s in summary] == ["cmp-q1"]
    return t, sink, a, z, (b1_branch, b2_branch, b3_branch)


def test_diamond_merges_across_compaction_then_retraction(app, hub):
    t, sink, a, z, (b1, b2, b3) = diamond(app, hub)
    assert app.merge(b1, tenant=t).json()["promoted"] == ["b1"]  # crosses the compacted horizon
    assert app.merge(b2, tenant=t).json()["promoted"] == ["b2"]
    d = app.add(
        "grant:d", {"payee": "p", "amount": "5.00"}, 180, tenant=t, id="d", parents=["b1", "b2"]
    )
    app.seal(180, 180, ident="q2", tenant=t)
    before = app.now
    compactions = app.get("/api/compactions", t).json()
    assert lineage(app, t, "d") == {
        "id": "d",
        "ancestors": ["z", "a", "b1", "b2"],
        "descendants": [],
    }
    assert lineage(app, t, "z")["descendants"] == ["a", "b1", "b2", "d"]
    at = app.t()
    assert app.retract("a", tenant=t, asserted_at=at).status_code == 200
    for ident in ("b1", "b2", "d"):
        record = app.record(ident, tenant=t)
        assert (record["status"], record["tainted_by"]) == ("tainted_pending_reconcile", ["a"]), (
            ident
        )
    assert app.record("z", tenant=t)["status"] == "active"
    bodies = wait_for(
        lambda: [
            json.loads(c["raw"])
            for c in hub.calls_to("sinks", f"/sinks/{sink}")
            if b"grant.invalidated" in c["raw"]
        ]
    )
    time.sleep(0.5)
    events = [
        json.loads(c["raw"])
        for c in hub.calls_to("sinks", f"/sinks/{sink}")
        if b"grant.invalidated" in c["raw"]
    ]
    assert events == [
        {
            "event": "grant.invalidated",
            "tenant": t,
            "assertion_id": d["id"],
            "seal_ids": ["q2"],
            "cause": "a",
            "asserted_at": at,
        }
    ]
    assert bodies
    # nothing about the compacted past changed, and reads before the retraction are as they were
    assert app.get("/api/compactions", t).json() == compactions
    assert app.get("/api/branches/main/seals/q1/verify", t).json()["valid"] is True
    assert app.record("d", tenant=t, asserted_at=before)["status"] == "active"
    assert lineage(app, t, "d", asserted_at=before)["ancestors"] == ["z", "a", "b1", "b2"]
    expect_error(app.retract("z", tenant=t), 409, "compacted")
    # the third line, forked before compaction and retraction, is quarantined as a whole
    expect_error(app.merge(b3, tenant=t), 409, "promotion_quarantined")
    receipt = app.get("/api/quarantine", t).json()["receipts"][-1]
    assert receipt["assertions"] == ["b3"] and receipt["retracted_parents"] == ["a"]
    expect_error(app.get("/api/branches/main/assertions/b3", t), 404, "not_found")


def test_retraction_between_promotions(app, hub):
    t, sink, a, z, (b1, b2, _) = diamond(app, hub)
    assert app.merge(b1, tenant=t).status_code == 200
    assert app.retract("a", tenant=t).status_code == 200
    state = app.state(tenant=t)
    expect_error(app.merge(b2, tenant=t), 409, "promotion_quarantined")
    assert app.state(tenant=t) == state
    assert lineage(app, t, "a")["descendants"] == ["b1"]
    assert lineage(app, t, "b2", branch=b2) == {
        "id": "b2",
        "ancestors": ["z", "a"],
        "descendants": [],
    }
    assert app.get("/api/branches/main/seals/q1/verify", t).json()["valid"] is True
    expect_error(app.get("/api/branches/main/assertions/nope/lineage", t), 404, "not_found")
    time.sleep(0.3)
    promoted = [
        json.loads(c["raw"])
        for c in hub.calls_to("sinks", f"/sinks/{sink}")
        if b"branch.promoted" in c["raw"]
    ]
    assert [p["branch"] for p in promoted] == [b1]
