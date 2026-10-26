"""The normal agent loop produces usable evidence without claiming correctness."""

from fastapi.testclient import TestClient

from anaxigraph.application import Application
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
    with TestClient(create_app(app)) as client:
        page = client.get("/api/evidence").json()
        assert page["events"]["total"] == len(rows)
    exported = app.export_evidence()
    assert exported["events"]
