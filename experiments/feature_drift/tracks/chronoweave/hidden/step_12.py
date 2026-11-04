"""Request 12: compaction of sealed horizons into read-only summaries, invisibly to reads."""

import hashlib
import hmac

from conftest import expect_error, sha256, uid


def compact(app, tenant, watermark, asserted_at=None):
    body = {"watermark": watermark, "asserted_at": asserted_at or app.t()}
    response = app.post("/api/compactions", tenant, json=body)
    assert response.status_code == 200, response.text
    return response.json()["compactions"]


def build(app, t):
    """Interval [0, 99] sealed with a superseded, a retracted and a live value; extras outside."""
    old = app.add("rate", 1, 10, tenant=t)
    new = app.add("rate", 2, 50, tenant=t, parents=[old["id"]])
    gone = app.add("tmp", "x", 60, tenant=t)
    assert app.retract(gone["id"], tenant=t).status_code == 200
    owner = app.add("owner", "ann", 99, tenant=t)
    later = app.add("rate", 3, 150, tenant=t, parents=[new["id"]])
    seal = app.seal(0, 99, ident="q1", tenant=t)
    app.seal(100, 199, ident="q2", tenant=t)
    return {"old": old, "new": new, "gone": gone, "owner": owner, "later": later, "seal": seal}


def test_summary(app):
    t = app.tenant()
    h = build(app, t)
    at = app.t()
    expect_error(
        app.post("/api/compactions", t, json={"watermark": -1, "asserted_at": app.t()}),
        422,
        "invalid_request",
    )
    expect_error(
        app.post("/api/compactions", t, json={"watermark": 100, "asserted_at": 1}),
        409,
        "clock_regression",
    )
    rows = compact(app, t, 100, at)
    provenance = sorted(
        (
            {
                "id": r["id"],
                "causal_parents": r["causal_parents"],
                "effective_at": r["effective_at"],
                "asserted_at": r["asserted_at"],
                "retracted_at": app.record(r["id"], tenant=t)["retracted_at"],
            }
            for r in (h["old"], h["new"], h["gone"], h["owner"])
        ),
        key=lambda row: row["id"],
    )
    assert rows == [
        {
            "id": "cmp-q1",
            "seal_id": "q1",
            "effective_from": 0,
            "effective_to": 99,
            "asserted_at": at,
            "assertion_count": 4,
            "entries": [
                {"key": "owner", "value": "ann", "assertion_id": h["owner"]["id"]},
                {"key": "rate", "value": 2, "assertion_id": h["new"]["id"]},
            ],
            "provenance_hash": sha256(provenance),
        }
    ]
    assert compact(app, t, 100) == []
    assert app.get("/api/compactions", t).json()["compactions"] == rows
    assert [c["seal_id"] for c in compact(app, t, 10_000)] == ["q2"]
    assert [c["id"] for c in app.get("/api/compactions", t).json()["compactions"]] == [
        "cmp-q1",
        "cmp-q2",
    ]


def test_reads_do_not_change(app):
    t = app.tenant()
    h = build(app, t)
    name = app.fork(tenant=t)
    app.add("rate", 9, 160, branch=name, tenant=t, parents=[h["later"]["id"]])
    early = h["new"]["asserted_at"]

    def reads():
        return (
            app.state(tenant=t),
            app.state(tenant=t, effective_at=55, asserted_at=early),
            app.state(tenant=t, effective_at=70),
            [app.record(h[k]["id"], tenant=t) for k in ("old", "gone", "later")],
            app.get("/api/branches/main/assertions", t).json(),
            app.get("/api/bundles/export", t).content,
            app.get("/api/branches/main/seals/q1/verify", t).json(),
            app.get(f"/api/branches/{name}/diff", t).json(),
        )

    before = reads()
    compact(app, t, 100)
    assert reads() == before
    app.restart()
    assert reads() == before
    assert [c["id"] for c in app.get("/api/compactions", t).json()["compactions"]] == ["cmp-q1"]
    assert app.get("/api/branches/main/seals/q1/verify", t).json()["valid"] is True


def test_compacted_interval_is_read_only(app):
    t = app.tenant()
    secret = "s-" + uid()
    app.put("/api/authority", t, json={"secret": secret})
    parent = app.add("p", 1, 500, tenant=t)
    child = app.add("c", 1, 20, tenant=t, parents=[parent["id"]])
    plain = app.add("d", 1, 30, tenant=t)
    assert app.retract(parent["id"], tenant=t).status_code == 200  # child tainted before compaction
    app.seal(0, 99, ident="q1", tenant=t)
    compact(app, t, 100)

    def proof(action, ident):
        return hmac.new(
            secret.encode(), f"{action}:q1:{ident}".encode(), hashlib.sha256
        ).hexdigest()

    ident = uid("n-")
    override = {"seal_id": "q1", "proof": proof("assert", ident)}
    expect_error(app.write("x", 1, 50, id=ident, tenant=t, override=override), 409, "compacted")
    expect_error(app.write("x", 1, 50, tenant=t), 409, "compacted")
    override = {"seal_id": "q1", "proof": proof("retract", plain["id"])}
    expect_error(app.retract(plain["id"], tenant=t, override=override), 409, "compacted")
    response = app.post(
        "/api/branches/main/checkpoints",
        t,
        json={"assertion_id": plain["id"], "asserted_at": app.t()},
    )
    expect_error(response, 409, "compacted")
    assert app.record(child["id"], tenant=t)["status"] == "tainted_pending_reconcile"
    response = app.post(
        f"/api/branches/main/assertions/{child['id']}/reconcile", t, json={"asserted_at": app.t()}
    )
    expect_error(response, 409, "compacted")
    assert app.write("x", 1, 100, tenant=t).status_code == 201


def test_compaction_insulates_later_taint(app):
    t = app.tenant()
    root = app.add("root", 1, 500, tenant=t)
    inside = app.add("in", 1, 20, tenant=t, parents=[root["id"]])
    below = app.add("below", 1, 600, tenant=t, parents=[inside["id"]])
    direct = app.add("direct", 1, 700, tenant=t, parents=[root["id"]])
    app.seal(0, 99, ident="q1", tenant=t)
    compact(app, t, 100)
    before = app.now
    assert app.retract(root["id"], tenant=t).status_code == 200
    assert app.record(inside["id"], tenant=t)["status"] == "active"
    assert app.record(below["id"], tenant=t)["status"] == "active"
    assert app.record(direct["id"], tenant=t)["tainted_by"] == [root["id"]]
    assert app.record(direct["id"], tenant=t, asserted_at=before)["status"] == "active"


def test_branches_forked_before_compaction(app):
    t = app.tenant()
    anchor = app.add("anchor", 1, 10, tenant=t)
    ok = app.fork(tenant=t)
    bad = app.fork(tenant=t)
    child = app.add("child", 2, 300, branch=ok, tenant=t, parents=[anchor["id"]])
    app.add("late", 2, 40, branch=bad, tenant=t)
    app.seal(0, 99, ident="q1", tenant=t)
    compact(app, t, 100)
    diff = app.get(f"/api/branches/{ok}/diff", t).json()
    assert diff["assertions"] == [child["id"]]
    response = app.merge(ok, tenant=t)
    assert response.status_code == 200, response.text
    assert response.json()["promoted"] == [child["id"]]
    assert app.record(child["id"], tenant=t)["causal_parents"] == [anchor["id"]]
    expect_error(app.merge(bad, tenant=t), 409, "compacted")
