"""All interfaces expose the same completion gate; CI receives a failing exit code."""

import asyncio
import json

import pytest
import test_ci_validation_gate as gate
from fastapi.testclient import TestClient
from test_ci_validation_gate import RUNTIME, selection_report, write_report

from anaxigraph.interfaces.cli import main
from anaxigraph.interfaces.http import create_app
from anaxigraph.interfaces.mcp import create_mcp


@pytest.fixture
def candidate(tmp_path):
    return gate.candidate.__wrapped__(tmp_path)


def payload(application, baseline):
    return {
        "baseline_id": baseline,
        "runtime_context": RUNTIME,
        "require_ci_evidence": True,
        "receipts": [
            {"command": "pytest", "snapshot_id": application._current().snapshot.id, "exit_code": 0}
        ],
    }


@pytest.mark.parametrize("channel", ["http", "mcp"])
def test_transports_forward_environment_and_required_evidence(candidate, channel):
    application, baseline = candidate
    write_report(application, selection_report(application))

    def request(body):
        if channel == "http":
            with TestClient(
                create_app(application), base_url="http://127.0.0.1", client=("127.0.0.1", 12345)
            ) as client:
                response = client.post("/api/validate", json=body)
                assert response.status_code == 200
                return response.json()
        result = asyncio.run(create_mcp(application).call_tool("validate_change", body))
        if isinstance(result, tuple):
            return result[1]
        return result if isinstance(result, dict) else json.loads(result[0].text)

    body = payload(application, baseline)
    accepted = request(body)
    assert accepted["completion_allowed"] is True
    assert accepted["ci_selection"]["environment_status"] == "matched"
    rejected = request(body | {"runtime_context": {"python": "different"}})
    assert rejected["validation_status"] == "incomplete"
    assert rejected["completion_allowed"] is False
    assert rejected["checks"][0]["status"] == "passed"
    assert rejected["behavioral_status"] != "reported-passed"


@pytest.mark.parametrize("problem", ["none", "gap", "missing", "unbound"])
def test_ci_cli_exit_code_refuses_unsafe_or_unqualified_validation(candidate, capsys, problem):
    application, baseline = candidate
    if problem != "missing":
        value = selection_report(application)
        if problem == "gap":
            value["files"][0]["selected"] = [0]
        write_report(application, value)
    body = payload(application, baseline)
    receipts = application.root.parent / "receipts.json"
    environment = application.root.parent / "environment.json"
    receipts.write_text(json.dumps(body["receipts"]))
    environment.write_text(json.dumps(RUNTIME))
    argv = [
        "--repo",
        str(application.root),
        "validate",
        "--baseline-id",
        baseline,
        "--receipts",
        str(receipts),
        "--require-ci-evidence",
    ]
    if problem != "unbound":
        argv += ["--runtime-context", str(environment)]
    exit_code = main(argv)
    result = json.loads(capsys.readouterr().out)
    assert exit_code == (0 if problem == "none" else 1)
    assert result["completion_allowed"] is (problem == "none")
    assert result["checks"][0]["status"] == "passed"
