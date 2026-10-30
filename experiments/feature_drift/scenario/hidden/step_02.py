import hub as h
from conftest import calls, error_code


def ours(row):
    return {
        "id": row["id"],
        "name": row["full_name"],
        "email": row["email"],
        "company": (row["org"] or {}).get("name"),
    }


def matching(term, dataset="a"):
    term = term.lower()
    return [
        ours(r)
        for r in h.contacts(dataset)
        if term in r["full_name"].lower()
        or term in r["email"].lower()
        or term in ((r["org"] or {}).get("name") or "").lower()
    ]


def test_search_follows_every_page(app, hub):
    app.creds("crm")
    body = app.get("/api/contacts").json()
    assert body == {"contacts": matching(""), "total": 25}
    assert len(calls(hub, "crm")) == 3
    assert {c["headers"]["authorization"] for c in calls(hub, "crm")} == {"Token crm-token-a"}


def test_search_term_and_limit(app, hub):
    app.creds("crm")
    expected = matching("ann")
    assert expected, "fixture must have matches"
    assert app.get("/api/contacts", params={"search": "ann"}).json() == {
        "contacts": expected,
        "total": len(expected),
    }
    body = app.get("/api/contacts", params={"limit": 5}).json()
    assert body["contacts"] == matching("")[:5] and body["total"] == 25
    for limit in (0, 201):
        response = app.get("/api/contacts", params={"limit": limit})
        assert response.status_code == 422 and error_code(response) == "invalid_request"


def test_company_is_null_without_org(app, hub):
    app.creds("crm")
    contacts = app.get("/api/contacts", params={"limit": 200}).json()["contacts"]
    assert any(c["company"] is None for c in contacts)
    assert any(c["company"] == "Acme Ltd" for c in contacts)


def test_create_contact(app, hub):
    app.creds("crm")
    response = app.post(
        "/api/contacts", json={"name": "Zed Quill", "email": "zed@quill.test", "company": "Quill"}
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert {k: created[k] for k in ("name", "email", "company")} == {
        "name": "Zed Quill",
        "email": "zed@quill.test",
        "company": "Quill",
    }
    assert created["id"]
    sent = [c for c in calls(hub, "crm") if c["method"] == "POST"][0]["body"]
    assert sent == {"full_name": "Zed Quill", "email": "zed@quill.test", "org_name": "Quill"} or (
        sent["full_name"] == "Zed Quill" and sent["email"] == "zed@quill.test"
    )
    found = app.get("/api/contacts", params={"search": "quill"}).json()
    assert found["contacts"] == [created]


def test_create_contact_without_company(app, hub):
    app.creds("crm")
    response = app.post("/api/contacts", json={"name": "Solo Person", "email": "solo@p.test"})
    assert response.status_code == 201 and response.json()["company"] is None


def test_create_contact_errors(app, hub):
    app.creds("crm")
    existing = h.contacts("a")[0]
    response = app.post("/api/contacts", json={"name": "Dup", "email": existing["email"]})
    assert response.status_code == 409 and error_code(response) == "conflict"
    for body in (
        {"name": "No At", "email": "nope"},
        {"email": "x@y.test"},
        {"name": "", "email": "a@b.c"},
    ):
        response = app.post("/api/contacts", json=body)
        assert response.status_code == 422 and error_code(response) == "invalid_request"


def test_auth_and_configuration(fresh_app, hub):
    response = fresh_app.get("/api/contacts")
    assert response.status_code == 409 and error_code(response) == "not_configured"
    fresh_app.put("/api/credentials/crm", json={"token": "nope"})
    response = fresh_app.get("/api/contacts")
    assert response.status_code == 502 and error_code(response) == "upstream_auth"
