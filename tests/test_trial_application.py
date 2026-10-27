"""The normal agent loop produces usable evidence without claiming correctness."""

from fastapi.testclient import TestClient

from anaxigraph.application import Application
from anaxigraph.evidence import Evidence
from anaxigraph.interfaces.http import create_app


def test_prepare_inspect_validate_and_export_record_the_actual_task(tmp_path):
    (tmp_path / "billing.py").write_text("def invoice():\n    return 1\n")
    app = Application(tmp_path)
    app.scan()
    assert not app.evidence.path.exists()
    brief = app.prepare_change("Add CSV export")
    app.inspect("module:billing.py")
    app.validate_change()
    rows = app.evidence.events()["items"]
    assert {row["kind"] for row in rows} >= {
        "task_started",
        "context_supplied",
        "context_consulted",
        "task_outcome",
    }
    assert all(row["task_id"] == brief["task_id"] for row in rows)
    supplied = next(row for row in rows if row["kind"] == "context_supplied")
    inspected = next(row for row in rows if row["kind"] == "context_consulted")
    assert supplied["payload"]["operation"] == "prepare_change"
    assert inspected["payload"]["entity_id"] == "module:billing.py"
    assert (
        next(row for row in rows if row["kind"] == "task_outcome")["payload"]["status"]
        == "validation_reported"
    )
    with TestClient(
        create_app(app), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    ) as client:
        page = client.get("/api/evidence").json()
        assert page["events"]["total"] == len(rows)
    exported = app.export_evidence()
    assert exported["events"]


def test_full_size_ledger_prunes_before_it_can_break_core_tools(tmp_path):
    (tmp_path / "service.py").write_text("def service(): return 1\n")
    app = Application(tmp_path)
    app.scan()
    app.prepare_change("Improve service")
    payload = {"metrics": {str(n): "x" * 4000 for n in range(3)}}
    for _ in range(450):
        app.evidence.record("probe", payload)
    assert app.evidence.summary()["dropped_events"] > 0
    assert app.evidence.path.stat().st_size <= app.evidence.max_bytes
    assert app.prepare_change("Improve service")["task_id"]
    assert app.inspect("module:service.py")["entity"]
    assert app.assess_plan("Improve service implementation", ["service.py"])["brief"]
    assert app.validate_change()["candidate_snapshot_id"]
    assert Evidence(tmp_path).events()["items"]
