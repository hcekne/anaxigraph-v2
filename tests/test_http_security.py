"""Untrusted HTTP requests cannot reach repository mutations or model job launchers."""

import base64
import socket
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from anaxigraph.interfaces.cli import main
from anaxigraph.interfaces.http import create_app

TOKEN = "a-random-service-token-with-at-least-32-characters"
LAN = "http://192.0.2.10:8766"


class Probe:
    def __init__(self, root):
        self.root, self.calls = root, []

    def status(self):
        return {"ready": True}

    def scan(self):
        self.calls.append("scan")
        (self.root / "mutation").write_text("mapped")
        return {"mapped": True}

    def workflow_start(self, *args):
        self.calls.append("model-job")
        (self.root / "model-job").write_text("launched")
        return {"started": True}

    def export_record(self):
        self.calls.append("export")
        (self.root / "exported").write_text("record")
        return {"exported": True}


def local_client(application, origin="http://127.0.0.1:8766", **options):
    return TestClient(
        create_app(application, **options), base_url=origin, client=("127.0.0.1", 50000)
    )


@pytest.mark.parametrize(
    "origin", ["http://127.0.0.1:8766", "http://localhost:8766", "http://[::1]:8766"]
)
def test_same_origin_dashboard_and_no_origin_local_cli_keep_working(tmp_path, origin):
    app = Probe(tmp_path)
    with local_client(app, origin) as client:
        assert client.get("/").status_code == 200
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/api/status").json() == {"ready": True}
        assert client.post("/api/scan", headers={"Origin": origin}).status_code == 200
        assert client.post("/api/record/export").status_code == 200
    assert app.calls == ["scan", "export"]


@pytest.mark.parametrize(
    "origin",
    [
        "https://attacker.example",
        "http://localhost:9999",
        "null",
        "http://127.0.0.1@attacker.example",
        "http://localhost/path",
    ],
)
@pytest.mark.parametrize(
    "path,body",
    [("/api/scan", {}), ("/api/workflows/start", {"kind": "mapping"}), ("/api/record/export", {})],
)
def test_cross_origin_posts_are_rejected_before_writes_or_model_jobs(tmp_path, origin, path, body):
    app = Probe(tmp_path)
    with local_client(app) as client:
        response = client.post(path, headers={"Origin": origin}, json=body)
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers
    assert app.calls == []
    assert not list(tmp_path.iterdir())


def test_cross_site_simple_form_post_and_missing_origin_browser_request_are_rejected(tmp_path):
    app = Probe(tmp_path)
    with local_client(app) as client:
        assert (
            client.post(
                "/api/scan",
                data={"anything": "value"},
                headers={"Origin": "https://attacker.example"},
            ).status_code
            == 403
        )
        assert client.post("/api/scan", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert app.calls == []
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "host",
    [
        "attacker.example",
        "localhost.attacker.example",
        "127.0.0.1.attacker.example",
        "localhost:invalid",
        "@localhost",
        "localhost/",
        "localhost/path",
    ],
)
def test_dns_rebinding_and_malformed_hosts_are_rejected(tmp_path, host):
    app = Probe(tmp_path)
    with local_client(app) as client:
        assert client.post("/api/scan", headers={"Host": host}).status_code in {403, 421}
    assert app.calls == []


def test_duplicate_host_or_origin_headers_are_rejected(tmp_path):
    app = Probe(tmp_path)
    with local_client(app) as client:
        assert (
            client.post(
                "/api/scan", headers=[("Host", "127.0.0.1"), ("Host", "attacker.example")]
            ).status_code
            == 421
        )
        assert (
            client.post(
                "/api/scan",
                headers=[
                    ("Origin", "http://127.0.0.1:8766"),
                    ("Origin", "https://attacker.example"),
                ],
            ).status_code
            == 421
        )
    assert app.calls == []


def test_remote_peer_cannot_spoof_loopback_host_on_an_unprotected_broad_binding(tmp_path):
    app = Probe(tmp_path)
    with TestClient(
        create_app(app), base_url="http://127.0.0.1:8766", client=("192.0.2.20", 50000)
    ) as client:
        assert client.post("/api/scan").status_code == 403
    assert app.calls == []


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Bearer wrong",
        "Basic invalid",
        "Basic " + base64.b64encode(("wrong:" + TOKEN).encode()).decode(),
    ],
)
def test_lan_access_requires_configured_authentication(tmp_path, authorization):
    app = Probe(tmp_path)
    with TestClient(
        create_app(app, allowed_origins=[LAN], auth_token=TOKEN),
        base_url=LAN,
        client=("192.0.2.20", 50000),
    ) as client:
        headers = {"Authorization": authorization} if authorization else {}
        response = client.post("/api/scan", headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"].startswith("Basic ")
    assert app.calls == []


@pytest.mark.parametrize(
    "authorization",
    ["Bearer " + TOKEN, "Basic " + base64.b64encode(("anaxigraph:" + TOKEN).encode()).decode()],
)
def test_lan_authenticated_ui_api_and_mcp_share_protection(tmp_path, authorization):
    app = Probe(tmp_path)
    with TestClient(
        create_app(app, allowed_origins=[LAN], auth_token=TOKEN),
        base_url=LAN,
        client=("192.0.2.20", 50000),
    ) as client:
        headers = {"Authorization": authorization, "Origin": LAN}
        assert client.get("/", headers=headers).status_code == 200
        assert client.post("/api/scan", headers=headers).status_code == 200
        headers["Accept"] = "application/json, text/event-stream"
        result = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "status", "arguments": {}},
            },
        )
        assert result.status_code == 200 and not result.json()["result"].get("isError")
        headers["Origin"] = "https://attacker.example"
        assert client.post("/api/scan", headers=headers).status_code == 403
        assert client.post("/mcp", headers=headers, json={}).status_code == 403
    assert app.calls == ["scan"]


@pytest.mark.parametrize("scheme,port", [("http", 80), ("https", 443)])
def test_explicit_default_port_authorities_work_for_rest_and_mcp_without_widening_scope(
    tmp_path, scheme, port
):
    origin = scheme + "://public.example"
    app = create_app(Probe(tmp_path), allowed_origins=[origin], auth_token=TOKEN)
    with TestClient(app, base_url=origin, client=("192.0.2.20", 50000)) as client:
        headers = {
            "Authorization": "Bearer " + TOKEN,
            "Host": f"public.example:{port}",
            "Origin": origin + f":{port}",
            "Accept": "application/json, text/event-stream",
        }
        assert client.get("/api/status", headers=headers).status_code == 200
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "status", "arguments": {}},
            },
        )
        assert response.status_code == 200 and not response.json()["result"].get("isError")
        headers["Host"] = "public.example:9999"
        assert client.get("/api/status", headers=headers).status_code == 421
        assert client.post("/mcp", headers=headers, json={}).status_code == 421


def test_insecure_remote_configuration_is_rejected(tmp_path):
    for origins, token in (([LAN], None), ([LAN], "short"), (["*"], TOKEN)):
        with pytest.raises(ValueError):
            create_app(Probe(tmp_path), allowed_origins=origins, auth_token=token)


def test_cli_lan_binding_needs_token_and_explicit_origin(tmp_path, monkeypatch, capsys):
    import uvicorn

    import anaxigraph.application

    app = Probe(tmp_path)
    calls = []
    monkeypatch.setattr(anaxigraph.application, "Application", lambda *args, **kwargs: app)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.delenv("ANAXIGRAPH_HTTP_TOKEN", raising=False)
    assert main(["serve"]) == 0
    assert calls[0][1] == {"host": "127.0.0.1", "port": 8766}
    calls.clear()
    arguments = ["serve", "--host", "0.0.0.0"]
    assert main(arguments) == 2 and calls == []
    monkeypatch.setenv("ANAXIGRAPH_HTTP_TOKEN", TOKEN)
    assert main(arguments) == 2 and calls == []
    assert main(arguments + ["--allow-origin", LAN]) == 0
    assert calls[0][1] == {"host": "0.0.0.0", "port": 8766}
    assert TOKEN not in capsys.readouterr().err


def test_actual_loopback_http_rejects_attacker_before_launching_jobs(tmp_path):
    import uvicorn

    app = Probe(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(app), log_level="error"))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                assert client.get("/").status_code == 200
                response = client.post(
                    "/api/workflows/start",
                    json={"kind": "mapping"},
                    headers={"Origin": "https://attacker.example"},
                )
                assert response.status_code == 403
                assert (
                    client.post("/api/scan", headers={"Host": "attacker.example"}).status_code
                    == 421
                )
                assert app.calls == [] and not list(tmp_path.iterdir())
                assert (
                    client.post(
                        "/api/scan", headers={"Origin": f"http://127.0.0.1:{port}"}
                    ).status_code
                    == 200
                )
                assert app.calls == ["scan"]
        finally:
            server.should_exit = True
            worker.join(5)
        assert not worker.is_alive()
