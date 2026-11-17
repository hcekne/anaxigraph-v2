"""A validation result cannot qualify checks for files edited during that validation."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from anaxigraph.application import Application
from anaxigraph.interfaces import cli
from anaxigraph.interfaces.http import create_app
from anaxigraph.interfaces.mcp import create_mcp


@pytest.mark.parametrize("channel", ["python", "http", "mcp", "cli"])
@pytest.mark.parametrize("concurrent_scan", [False, True])
def test_edit_during_validation_is_incomplete_and_preserves_the_task_baseline(
    tmp_path, monkeypatch, capsys, channel, concurrent_scan
):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "service.py"
    source.write_text("def status():\n    return 1\n")
    app = Application(root)
    baseline = app.prepare_change("Improve service")["baseline_id"]
    receipts = [{"command": "reported fixture check", "snapshot_id": baseline, "exit_code": 0}]
    original = app.store.stats
    edits = []

    def edit_after_capture(*args, **kwargs):
        result = original(*args, **kwargs)
        if not edits:
            edits.append(True)
            source.write_text("def status():\n    return 2\n")
            if concurrent_scan:
                Application(root).scan()
        return result

    monkeypatch.setattr(app.store, "stats", edit_after_capture)
    if channel == "http":
        with TestClient(
            create_app(app), base_url="http://127.0.0.1", client=("127.0.0.1", 12345)
        ) as client:
            response = client.post("/api/validate", json={"receipts": receipts})
            assert response.status_code == 200
            result = response.json()
    elif channel == "mcp":
        result = asyncio.run(create_mcp(app).call_tool("validate_change", {"receipts": receipts}))
        result = result[1] if isinstance(result, tuple) else result
        result = result if isinstance(result, dict) else json.loads(result[0].text)
    elif channel == "cli":
        supplied = tmp_path / "receipts.json"
        supplied.write_text(json.dumps(receipts))
        monkeypatch.setattr("anaxigraph.application.Application", lambda *args, **kwargs: app)
        assert cli.main(["--repo", str(root), "validate", "--receipts", str(supplied)]) == 0
        result = json.loads(capsys.readouterr().out)
    else:
        result = app.validate_change(receipts)
    assert result["candidate_snapshot_id"] == baseline
    assert result["checks"][0]["status"] == "passed"  # Attributed to that captured snapshot.
    assert result["completion_allowed"] is False
    assert result["validation_status"] == "incomplete"
    assert result["checkout_current"] is False
    assert app.evidence.latest("task_outcome")["status"] == "incomplete"
    assert "checkout_current=False" in app.evidence.latest("task_outcome")["notes"]
    assert original(str(root))["pinned_snapshot_id"] == baseline
    fresh = app.validate_change(receipts)
    assert fresh["candidate_snapshot_id"] != baseline
    assert fresh["baseline_id"] == baseline
    assert fresh["checks"][0]["status"] == "stale"
    assert fresh["checkout_current"] is True and not fresh["completion_allowed"]


def test_an_unchanged_baseline_reuses_the_captured_graph_and_retains_findings(
    tmp_path, monkeypatch
):
    (tmp_path / "large.py").write_text("\n".join(f"value_{i} = {i}" for i in range(605)))
    app = Application(tmp_path)
    baseline = app.prepare_change("Review large module")["baseline_id"]
    original = app.store.load_graph
    scans = []
    scan = app.indexer.scan

    def no_duplicate_load(root, snapshot_id=None):
        assert snapshot_id != baseline, "The captured graph already is this baseline"
        return original(root, snapshot_id)

    def counted_scan():
        scans.append(True)
        return scan()

    monkeypatch.setattr(app.store, "load_graph", no_duplicate_load)
    monkeypatch.setattr(app.indexer, "scan", counted_scan)
    result = app.validate_change(full=True)
    assert result["baseline_id"] == result["candidate_snapshot_id"] == baseline
    assert result["findings_summary"] == {"unchanged": 1}
    assert result["findings"][0]["measured"] == 605
    assert result["findings"][0]["previous_measured"] == 605
    assert result["checkout_current"] is True
    assert len(scans) == 1
