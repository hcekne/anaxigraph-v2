import hub as h
from conftest import error_code


def test_search(app, hub):
    app.creds("docs")
    response = app.get("/api/docs/search", params={"q": "deploy", "limit": 3})
    assert response.status_code == 200, response.text
    expected = hub.tool_docs("search_docs", {"query": "deploy", "limit": 3}, "a")
    assert response.json() == expected
    assert len(expected["results"]) == 3


def test_search_default_limit_and_validation(app, hub):
    app.creds("docs")
    body = app.get("/api/docs/search", params={"q": "deploy"}).json()
    assert body == hub.tool_docs("search_docs", {"query": "deploy", "limit": 5}, "a")
    assert app.get("/api/docs/search", params={"q": "zzzz"}).json() == {"results": []}
    for params in ({}, {"q": "deploy", "limit": 21}, {"q": "deploy", "limit": 0}):
        response = app.get("/api/docs/search", params=params)
        assert response.status_code == 422 and error_code(response) == "invalid_request"


def test_get_document(app, hub):
    app.creds("docs")
    doc = h.DOCS[2]
    assert app.get(f"/api/docs/{doc[0]}").json() == {
        "id": doc[0],
        "title": doc[1],
        "body": doc[2],
        "url": f"https://docs.internal/{doc[0]}",
    }
    response = app.get("/api/docs/doc-99")
    assert response.status_code == 404 and error_code(response) == "not_found"


def test_bearer_token_is_used(app, hub):
    app.creds("docs")
    app.get("/api/docs/search", params={"q": "flags"})
    from conftest import calls

    assert calls(hub, "docs")
    assert {c["headers"].get("authorization") for c in calls(hub, "docs")} == {"Bearer docs-token"}


def test_errors(fresh_app, hub):
    response = fresh_app.get("/api/docs/search", params={"q": "deploy"})
    assert response.status_code == 409 and error_code(response) == "not_configured"
    fresh_app.put("/api/credentials/docs", json={"token": "bad"})
    response = fresh_app.get("/api/docs/search", params={"q": "deploy"})
    assert response.status_code == 502 and error_code(response) == "upstream_auth"
