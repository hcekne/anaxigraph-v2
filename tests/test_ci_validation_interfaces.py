"""Every transport attributes current checks without an experiment-specific CI gate."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from anaxigraph.application import Application
from anaxigraph.interfaces.cli import main
from anaxigraph.interfaces.http import create_app
from anaxigraph.interfaces.mcp import create_mcp


@pytest.mark.parametrize("channel", ["http", "mcp", "cli"])
@pytest.mark.parametrize("problem", ["none", "failed", "stale", "missing"])
def test_transports_report_check_status_without_inferring_a_selector_protocol(
    tmp_path, channel, problem, capsys
):
    (tmp_path / "service.py").write_text("value = 1\n")
    (tmp_path / "ci.json").write_text('{"select": ["custom-selector"]}')
    app = Application(tmp_path)
    baseline = app.prepare_change("Improve service")["baseline_id"]
    (tmp_path / "service.py").write_text("value = 2\n")
    snapshot = app.scan()["snapshot"]["id"]
    body = {
        "baseline_id": baseline,
        "receipts": []
        if problem == "missing"
        else [
            {
                "command": "pytest",
                "snapshot_id": "old" if problem == "stale" else snapshot,
                "exit_code": 1 if problem == "failed" else 0,
            }
        ],
    }
    if channel == "http":
        with TestClient(
            create_app(app), base_url="http://127.0.0.1", client=("127.0.0.1", 12345)
        ) as client:
            response = client.post("/api/validate", json=body)
            assert response.status_code == 200
            result = response.json()
    elif channel == "mcp":
        result = asyncio.run(create_mcp(app).call_tool("validate_change", body))
        result = result[1] if isinstance(result, tuple) else result
        result = result if isinstance(result, dict) else json.loads(result[0].text)
    else:
        receipts = tmp_path.parent / "receipts.json"
        receipts.write_text(json.dumps(body["receipts"]))
        code = main(
            [
                "--repo",
                str(tmp_path),
                "validate",
                "--baseline-id",
                baseline,
                "--receipts",
                str(receipts),
            ]
        )
        result = json.loads(capsys.readouterr().out)
        assert code == (1 if problem == "failed" else 0)
    assert result["completion_allowed"] is (problem == "none")
    assert (
        result["validation_status"]
        == {
            "none": "reported-passed",
            "failed": "blocked",
            "stale": "incomplete",
            "missing": "unchecked",
        }[problem]
    )
    assert "ci_selection" not in result
    assert result["tests"]["status"] == "no_record"
