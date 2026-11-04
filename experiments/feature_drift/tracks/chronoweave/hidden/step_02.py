"""Request 2: epoch seals, digests, verification and HMAC override proofs."""

import hashlib
import hmac

from conftest import band, expect_error, sha256, uid

SECRET = "authority-secret-1"


def proof(action, seal_id, assertion_id, secret=SECRET):
    message = f"{action}:{seal_id}:{assertion_id}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def row(record):
    return {k: record[k] for k in ("id", "key", "value", "effective_at", "asserted_at")}


def test_no_secret_means_no_valid_override(fresh_app):
    app = fresh_app
    seal = app.seal(0, 100, ident="q0")
    ident = uid("w-")
    response = app.write(
        "k", 1, 50, id=ident, override={"seal_id": "q0", "proof": proof("assert", "q0", ident)}
    )
    expect_error(response, 403, "invalid_override")
    assert seal["digest"] == sha256([])


def test_seal_record_digest_and_verify(app):
    lo = band()
    p = uid("k") + ":"
    a1 = app.add(p + "a", {"v": 1}, lo + 10)
    a2 = app.add(p + "b", "two", lo + 999)
    gone = app.add(p + "c", 3, lo + 500)
    assert app.retract(gone["id"]).status_code == 200
    app.add(p + "d", 4, lo + 1000)  # outside the interval
    ident = uid("q-")
    t = app.t()
    response = app.post(
        "/api/branches/main/seals",
        json={"id": ident, "effective_from": lo, "effective_to": lo + 999, "asserted_at": t},
    )
    assert response.status_code == 201, response.text
    seal = response.json()
    expected = sha256(sorted([row(a1), row(a2)], key=lambda r: r["id"]))
    assert seal == {
        "id": ident,
        "effective_from": lo,
        "effective_to": lo + 999,
        "asserted_at": t,
        "digest": expected,
    }
    assert app.get(f"/api/branches/main/seals/{ident}").json() == seal
    listed = app.get("/api/branches/main/seals").json()["seals"]
    assert seal in listed and [s["id"] for s in listed] == sorted(s["id"] for s in listed)
    verify = app.get(f"/api/branches/main/seals/{ident}/verify").json()
    assert verify == {"id": ident, "valid": True, "digest": expected}
    expect_error(app.get(f"/api/branches/main/seals/{uid('none-')}"), 404, "not_found")

    def create(**changes):
        body = {
            "id": uid("q-"),
            "effective_from": lo,
            "effective_to": lo + 5,
            "asserted_at": app.t(),
            **changes,
        }
        return app.post("/api/branches/main/seals", json=body)

    expect_error(create(id=ident), 409, "conflict")
    expect_error(create(effective_from=lo + 6), 422, "invalid_request")
    expect_error(create(effective_to=-1), 422, "invalid_request")
    expect_error(create(asserted_at=t - 1), 409, "clock_regression")
    name = app.fork()
    response = app.post(
        f"/api/branches/{name}/seals",
        json={"id": uid("q-"), "effective_from": 0, "effective_to": 1, "asserted_at": app.t()},
    )
    expect_error(response, 409, "main_only")


def test_seal_freezes_interval(app):
    lo = band()
    p = uid("k") + ":"
    inside = app.add(p + "a", 1, lo + 50)
    seal = app.seal(lo, lo + 100)
    for effective in (lo, lo + 50, lo + 100):
        expect_error(app.write(p + "a", 2, effective), 409, "sealed_interval")
    assert app.write(p + "a", 2, lo + 101).status_code == 201
    assert app.write(p + "a", 2, lo - 1).status_code == 201
    expect_error(app.retract(inside["id"]), 409, "sealed_interval")
    assert app.record(inside["id"])["retracted_at"] is None
    # checkpoints and branches are not affected
    response = app.post(
        "/api/branches/main/checkpoints",
        json={"assertion_id": inside["id"], "asserted_at": app.t()},
    )
    assert response.status_code == 201
    name = app.fork()
    assert app.write(p + "a", 3, lo + 50, branch=name).status_code == 201
    assert app.get(f"/api/branches/main/seals/{seal['id']}/verify").json()["valid"] is True


def test_override_proofs(app):
    assert app.put("/api/authority", json={"secret": SECRET}).status_code == 204
    expect_error(app.put("/api/authority", json={"secret": ""}), 422, "invalid_request")
    lo = band()
    p = uid("k") + ":"
    target = app.add(p + "a", 1, lo + 10)
    s1 = app.seal(lo, lo + 100)
    s2 = app.seal(lo + 50, lo + 200)
    other = app.seal(lo + 300, lo + 400)

    def write(effective, seal_id, action="assert", secret=SECRET, ident=None):
        ident = ident or uid("o-")
        override = {"seal_id": seal_id, "proof": proof(action, seal_id, ident, secret)}
        return app.write(p + "a", 2, effective, id=ident, override=override)

    assert write(lo + 20, s1["id"]).status_code == 201
    assert write(lo + 60, s2["id"]).status_code == 201  # covered by both: either seal works
    assert write(lo + 60, s1["id"]).status_code == 201
    expect_error(write(lo + 150, s1["id"]), 403, "invalid_override")  # s1 does not cover it
    expect_error(write(lo + 20, other["id"]), 403, "invalid_override")
    expect_error(write(lo + 20, "no-such-seal"), 403, "invalid_override")
    expect_error(write(lo + 20, s1["id"], action="retract"), 403, "invalid_override")
    expect_error(write(lo + 20, s1["id"], secret="wrong"), 403, "invalid_override")
    # an override where none is needed is ignored
    assert write(lo + 250, s1["id"], secret="wrong").status_code == 201
    bad = {"seal_id": s1["id"], "proof": proof("assert", s1["id"], target["id"])}
    expect_error(app.retract(target["id"], override=bad), 403, "invalid_override")
    good = {"seal_id": s1["id"], "proof": proof("retract", s1["id"], target["id"])}
    response = app.retract(target["id"], override=good)
    assert response.status_code == 200 and response.json()["retracted_at"] is not None
    # history recorded before the seal is unchanged, so verification still holds
    for seal in (s1, s2):
        assert app.get(f"/api/branches/main/seals/{seal['id']}/verify").json()["valid"] is True
