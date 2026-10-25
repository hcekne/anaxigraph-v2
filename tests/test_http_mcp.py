"""Exercise real HTTP MCP lifecycle and tool calls through the dashboard server."""

import json

from fastapi.testclient import TestClient

from anaxigraph.application import Application
from anaxigraph.interfaces.http import create_app


def test_dashboard_and_stateless_mcp_share_the_same_application(tmp_path):
    (tmp_path / "billing.py").write_text("def invoice():\n    return 1\n")
    app = Application(tmp_path)
    app.scan()
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(create_app(app), base_url="http://127.0.0.1:8765") as client:
        assert client.get("/").status_code == 200
        initialized = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "cutover-test", "version": "1"},
                },
            },
        )
        assert initialized.status_code == 200
        assert initialized.json()["result"]["serverInfo"]["name"] == "AnaxiGraph V2"
        tools = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        )
        assert {tool["name"] for tool in tools.json()["result"]["tools"]} >= {
            "status",
            "prepare_change",
            "workflow_start",
            "export_record",
        }
        result = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "status", "arguments": {}},
            },
        ).json()["result"]
        assert not result.get("isError", False)
        # Older MCP clients negotiate text-only tool results.
        status = result.get("structuredContent") or json.loads(result["content"][0]["text"])
        assert status["root"] == str(tmp_path)
        assert status["snapshot"]["id"] == client.get("/api/status").json()["snapshot"]["id"]


def test_source_exclusions_apply_to_mapping_live_checks_and_exports(tmp_path):
    (tmp_path / "service.py").write_text("def invoice():\n    return 1\n")
    for folder in ("data", ".secrets"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "runtime.py").write_text("def runtime():\n    return 1\n")
    app = Application(tmp_path, excluded=("data/**", ".secrets/**"))
    assert app.scan()["modules"] == 1
    for folder in ("data", ".secrets"):
        (tmp_path / folder / "runtime.py").write_text("def runtime():\n    return 2\n")
    app._require_live(app._current())
    record = json.loads((tmp_path / "anaxigraph" / "record.json").read_text())
    assert not any(path.startswith(("data/", ".secrets/")) for path in record["source"]["files"])
    assert app.analysis_request("annotate", ["service.py"])["scope"] == ["module:service.py"]
