"""Request 17: five regional residency / right-to-erasure policy verifiers."""

from conftest import expect_error, uid

POLICIES = {
    # name: (region, residency enforced, erasure mode)
    "policy_eu": ("eu", True, "full"),
    "policy_us_ca": ("us-ca", False, "forward"),
    "policy_jp": ("jp", True, "forward"),
    "policy_br": ("br", True, "full"),
    "policy_sg": ("sg", False, "full"),
}


def enable(app, t, name, allowed_nodes):
    body = {"enabled": True, "config": {"allowed_nodes": allowed_nodes}}
    response = app.put(f"/api/verifiers/{name}", t, json=body)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "name": name,
        "enabled": True,
        "config": {"allowed_nodes": allowed_nodes},
    }


def test_listed_and_validated(app):
    t = app.tenant()
    rows = {r["name"]: r for r in app.get("/api/verifiers", t).json()["verifiers"]}
    for name in POLICIES:
        assert rows[name] == {"name": name, "enabled": False, "config": None}
    names = [r["name"] for r in app.get("/api/verifiers", t).json()["verifiers"]]
    assert names == sorted(names)
    assert {"escrow_hold", "gdpr_retention", "geofence_jurisdiction", "burst_rate_cap"} <= set(
        names
    )
    for bad in ({}, {"allowed_nodes": "eu-1"}, {"allowed_nodes": [1]}):
        response = app.put("/api/verifiers/policy_eu", t, json={"enabled": True, "config": bad})
        expect_error(response, 422, "invalid_request")
    enable(app, t, "policy_eu", None)


def test_residency(app):
    for name, (region, residency, _) in POLICIES.items():
        t = app.tenant()
        enable(app, t, name, ["home-1"])
        key = f"pii:{region}:{uid('s')}:email"
        assert app.write(key, "a@x", 10, tenant=t, node="home-1").status_code == 201
        response = app.write(key, "a@x", 11, tenant=t, node="elsewhere")
        if residency:
            expect_error(response, 409, "residency_violation")
        else:
            assert response.status_code == 201, (name, response.text)
        # other regions and non-personal keys are not this policy's business
        other = "eu" if region != "eu" else "jp"
        assert (
            app.write(f"pii:{other}:s:email", 1, 1, tenant=t, node="elsewhere").status_code == 201
        )
        assert app.write("ops:x", 1, 1, tenant=t, node="elsewhere").status_code == 201


def test_erasure(app):
    for name, (region, _, mode) in POLICIES.items():
        t = app.tenant()
        enable(app, t, name, None)
        subject = uid("s")
        key = f"pii:{region}:{subject}:phone"
        assert app.write(key, "1", 100, tenant=t).status_code == 201
        app.add(f"erasure:{region}:{subject}", True, 200, tenant=t)
        app.add(f"erasure:{region}:{subject}", True, 300, tenant=t)
        back_dated = app.write(key, "2", 150, tenant=t)
        forward = app.write(key, "3", 250, tenant=t)
        expect_error(forward, 409, "erasure_requested")
        if mode == "full":
            expect_error(back_dated, 409, "erasure_requested")
        else:
            assert back_dated.status_code == 201, (name, back_dated.text)
        # another subject is unaffected
        assert app.write(f"pii:{region}:{uid('o')}:phone", "x", 250, tenant=t).status_code == 201


def test_erasure_visibility_and_merges(app):
    t = app.tenant()
    enable(app, t, "policy_eu", None)
    subject = uid("s")
    key = f"pii:eu:{subject}:name"
    not_a_request = app.add(f"erasure:eu:{subject}", "maybe", 10, tenant=t)
    assert app.write(key, "ann", 50, tenant=t).status_code == 201
    name = app.fork(tenant=t)
    app.add(key, "ann b", 60, branch=name, tenant=t)
    request = app.add(f"erasure:eu:{subject}", True, 10, tenant=t)
    expect_error(app.write(key, "x", 70, tenant=t), 409, "erasure_requested")
    expect_error(app.merge(name, tenant=t), 409, "erasure_requested")
    assert app.retract(request["id"], tenant=t).status_code == 200
    assert app.write(key, "x", 70, tenant=t).status_code == 201
    assert not_a_request["value"] == "maybe"
    # existing verifiers keep working next to the new ones
    app.put(
        "/api/verifiers/gdpr_retention",
        t,
        json={"enabled": True, "config": {"prefixes": ["pii:"], "max_age_days": 1}},
    )
    now = app.t(10**6)
    expect_error(
        app.write(key, "y", now - 2 * 86400, tenant=t, asserted_at=now), 409, "retention_expired"
    )
