"""Old observations cannot hide new regressions; expansion preserves the complete comparison."""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anaxigraph.analysis.changes import change_review
from anaxigraph.application import Application
from anaxigraph.contracts import Edge, Graph, Node, Snapshot
from anaxigraph.indexing import index
from anaxigraph.interfaces.cli import dispatch, parser
from anaxigraph.interfaces.http import create_app
from anaxigraph.interfaces.mcp import create_mcp


def large_module(root, name):
    (root / name).write_text("\n".join(f"value_{i} = {i}" for i in range(601)) + "\n")


def test_new_boundary_regressions_survive_more_than_fifty_existing_warnings(tmp_path):
    for i in range(60):
        large_module(tmp_path, f"legacy_{i:02}.py")
    (tmp_path / "a.py").write_text("from z import run\ndef _secret():\n    return 1\n")
    (tmp_path / "z.py").write_text("def run():\n    return 1\n")
    app = Application(tmp_path)
    baseline = app.prepare_change("Preserve the existing dependency direction")["baseline_id"]
    (tmp_path / "z.py").write_text("from a import _secret\ndef run():\n    return _secret()\n")
    compact = app.validate_change()
    assert compact["baseline_id"] == baseline
    assert compact["findings_summary"] == {"introduced": 3, "unchanged": 60}
    assert compact["findings_total"] == 63 and not compact["findings_truncated"]
    assert {(item["kind"], item["entity"]) for item in compact["findings"]} == {
        ("dependency-cycle", "module:a.py"),
        ("dependency-cycle", "module:z.py"),
        ("private-interface-access", "module:z.py"),
    }
    assert all(item["change"] == "introduced" and item["evidence"] for item in compact["findings"])
    full = app.validate_change(**compact["findings_expand"])
    assert full["findings_summary"] == compact["findings_summary"]
    assert full["base_snapshot_id"] == baseline
    assert full["candidate_snapshot_id"] == compact["candidate_snapshot_id"]
    assert len(full["findings"]) == 63 and full["findings"][:3] == compact["findings"]
    assert len(json.dumps(compact["findings"])) < len(json.dumps(full["findings"])) / 10
    assert full["tests"] == compact["tests"] and full["checks"] == compact["checks"]


def test_many_new_findings_advertise_truncation_and_allow_complete_expansion(tmp_path):
    (tmp_path / "anchor.py").write_text("value = 1\n")
    before = index(tmp_path)
    for i in range(55):
        large_module(tmp_path, f"new_{i}.py")
    graph = index(tmp_path)
    compact = change_review(graph, before, None)
    assert len(compact["findings"]) == 50 and compact["findings_truncated"]
    assert compact["findings_summary"] == {"introduced": 55}
    full = change_review(graph, before, None, full=True)
    assert len(full["findings"]) == 55 and not full["findings_truncated"]
    assert full["findings_total"] == compact["findings_total"] == 55


def test_changed_boundary_evidence_is_not_hidden_when_the_violation_count_stays_equal():
    modules = tuple(Node(f"module:{name}.py", "module", name, f"{name}.py") for name in "abc")
    private = tuple(
        Node(f"function:{name}.py:_entry", "function", "_entry", f"{name}.py") for name in "bc"
    )
    before = Graph(
        Snapshot("before", "/fixture", None, False, {}, "now"),
        (*modules, *private),
        (Edge(modules[0].id, private[0].id, "calls", "a.py"),),
    )
    after = Graph(
        Snapshot("after", "/fixture", None, False, {}, "now"),
        before.nodes,
        (Edge(modules[0].id, private[1].id, "calls", "a.py"),),
    )
    review = change_review(after, before, None)
    assert review["findings_summary"] == {"changed": 1}
    finding = review["findings"][0]
    assert finding["measured"] == finding["previous_measured"] == 1
    assert finding["evidence"][0]["target"] == private[1].id


@pytest.mark.parametrize("transport", ["python", "cli", "mcp", "http"])
def test_unchanged_findings_expand_through_each_real_transport(tmp_path, transport):
    large_module(tmp_path, "owner.py")
    app = Application(tmp_path)
    baseline = app.prepare_change("Inspect current responsibilities")["baseline_id"]

    def validate(full):
        if transport == "cli":
            return dispatch(app, parser().parse_args(["validate"] + (["--full"] if full else [])))
        if transport == "http":
            with TestClient(
                create_app(app),
                base_url="http://127.0.0.1",
                client=("127.0.0.1", 50000),
            ) as client:
                response = client.post("/api/validate", json={"full": full})
                assert response.status_code == 200
                assert 'id="validation-full"' in client.get("/").text
                return response.json()
        if transport == "mcp":
            content = asyncio.run(create_mcp(app).call_tool("validate_change", {"full": full}))
            return json.loads(content[0].text)
        return app.validate_change(full=full)

    compact, full = validate(False), validate(True)
    assert compact["findings"] == []
    assert compact["findings_summary"] == {"unchanged": 1}
    assert compact["findings_expand"] == {"full": True, "baseline_id": baseline}
    assert full["findings"][0]["change"] == "unchanged"
    assert full["findings_total"] == compact["findings_total"] == 1
    assert full["baseline_id"] == compact["baseline_id"] == baseline
    assert full["tests"] == compact["tests"]


def test_dashboard_validation_toggle_uses_the_real_http_payloads(tmp_path):
    large_module(tmp_path, "owner.py")
    app = Application(tmp_path)
    app.prepare_change("Preserve responsibilities")
    with TestClient(
        create_app(app), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    ) as client:
        compact = client.post("/api/validate", json={}).json()
        receipts = [
            {"command": "pytest", "snapshot_id": compact["candidate_snapshot_id"], "exit_code": 0}
        ]
        full = client.post("/api/validate", json={"full": True, "receipts": receipts}).json()
    packets = tmp_path / "packets.json"
    packets.write_text(json.dumps({"receipts": receipts, "results": [compact, full]}))
    subprocess.run(
        ["node", str(Path(__file__).with_name("validation_dashboard.cjs")), str(packets)],
        check=True,
        capture_output=True,
        text=True,
    )
