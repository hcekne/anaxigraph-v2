from fastapi.testclient import TestClient

from switchboard.app import create_app
from switchboard.settings import Settings


def client(tmp_path) -> TestClient:
    return TestClient(create_app(Settings("http://provider.test", tmp_path)))


def test_health(tmp_path):
    assert client(tmp_path).get("/api/health").json() == {"status": "ok"}


def test_validation_errors_use_the_envelope(tmp_path):
    app = create_app(Settings("http://provider.test", tmp_path))

    @app.get("/api/echo")
    def echo(count: int) -> dict:
        return {"count": count}

    response = TestClient(app).get("/api/echo?count=x")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
