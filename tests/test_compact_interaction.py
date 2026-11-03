"""Compact views retain their evidence and expose executable expansion contracts."""

import json

from fastapi.testclient import TestClient
from test_interfaces import RecordingApplication

from anaxigraph.analysis.guidance import neighborhood, prepare
from anaxigraph.application import Application
from anaxigraph.contracts import Edge, Graph, Node, Snapshot
from anaxigraph.interfaces.cli import dispatch, parser
from anaxigraph.interfaces.http import create_app


def test_neighborhood_pages_all_relations_with_their_original_evidence():
    owner = Node(
        "module:owner.py",
        "module",
        "owner",
        "owner.py",
        data={
            "annotation_status": "current",
            "annotation": {
                "overview": "Owner purpose " * 1000,
                "detail": "Large explanation " * 3000,
                "contracts": [
                    {
                        "statement": "Preserve isolation",
                        "basis": "declared",
                        "evidence": [{"path": "owner.py", "line": 1}],
                    }
                ],
            },
        },
    )
    callers = tuple(
        Node(f"module:caller_{i}.py", "module", f"caller_{i}", f"caller_{i}.py") for i in range(80)
    )
    graph = Graph(
        Snapshot(
            "current", "/fixture", None, False, {n.path: "hash" for n in (owner, *callers)}, "now"
        ),
        (owner, *callers),
        tuple(Edge(n.id, owner.id, "imports", n.path) for n in callers)
        + tuple(Edge(owner.id, n.id, "imports", owner.path) for n in callers[:40]),
    )
    found, offset = [], 0
    while True:
        result = neighborhood(graph, owner.id, limit=30, offset=offset)
        assert len(json.dumps(result, indent=2).encode()) <= 8000
        assert result["contracts"][0]["evidence"] == [{"path": "owner.py", "line": 1}]
        assert "detail" not in result["entity"]["data"]["annotation"]
        relations = result["incoming"] + result["outgoing"]
        found += relations
        assert {n["id"] for n in result["neighbors"]} == (
            {e["source"] for e in relations} | {e["target"] for e in relations}
        ) - {owner.id}
        offset = result["next_offset"]
        if offset is None:
            break
    assert len(found) == len({(e["source"], e["target"]) for e in found}) == 120
    assert all(e["projected_from"]["source"] == e["source"] for e in found)
    assert neighborhood(graph, owner.id, full=True)["entity"]["data"] == owner.data


def test_large_brief_preserves_three_owners_and_essential_contract_evidence():
    nodes = tuple(
        Node(
            f"module:{i}.py",
            "module",
            str(i),
            f"{i}.py",
            data={
                "annotation_status": "current",
                "annotation": {
                    "overview": "Purpose " * 1000,
                    "contracts": [
                        {
                            "statement": "Essential contract " * 100,
                            "basis": "declared",
                            "evidence": [{"path": f"{i}.py", "line": 1}],
                        }
                    ],
                },
            },
        )
        for i in range(3)
    )
    graph = Graph(
        Snapshot("current", "/fixture", None, False, {n.path: "hash" for n in nodes}, "now"),
        nodes,
        (),
    )
    matches = [
        {"id": n.id, "path": n.path, "evidence": [{"path": n.path, "line": 1}]} for n in nodes
    ]
    brief = prepare(graph, matches, "Change purpose " * 200)
    assert len(brief["likely_owners"]) == len(brief["contracts"]) == 3
    assert all(item["evidence"] for item in brief["likely_owners"] + brief["contracts"])


def test_assessment_delta_keeps_task_and_original_baseline_after_scan(tmp_path):
    source = tmp_path / "owner.py"
    source.write_text("def owner(): return 1\n")
    app = Application(tmp_path)
    app.scan()
    original = app.prepare_change("Improve owner")
    source.write_text("def owner(): return 2\n")
    app.scan()
    delta = app.assess_plan("Improve owner implementation", ["owner.py"])
    assert "brief" not in delta
    assert delta["task_id"] == original["task_id"]
    assert delta["baseline_id"] == original["baseline_id"]
    assert delta["snapshot_id"] != original["snapshot_id"]
    expanded = app.assess_plan("Improve owner implementation", ["owner.py"], include_brief=True)
    assert expanded["brief"]["baseline_id"] == original["baseline_id"]
    assert expanded["brief"]["task_id"] == original["task_id"]


def test_cli_exposes_paging_and_explicit_expansion():
    app = RecordingApplication()
    dispatch(app, parser().parse_args(["inspect", "owner.py", "--offset", "4", "--full"]))
    assert app.calls[-1] == ("inspect", ("owner.py", 30, 4, True))
    dispatch(
        app,
        parser().parse_args(["assess", "Improve owner", "--files", "owner.py", "--include-brief"]),
    )
    assert app.calls[-1] == ("assess_plan", ("Improve owner", ["owner.py"], None, True))


def test_real_mcp_schema_rejects_malformed_inputs_before_application_work():
    app = RecordingApplication()
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(
        create_app(app), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    ) as client:

        def rpc(method, params):
            return client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params,
                },
            ).json()["result"]

        tools = {t["name"]: t for t in rpc("tools/list", {})["tools"]}
        assert len(tools) == 20
        receipt = tools["validate_change"]["inputSchema"]["$defs"]["CheckReceipt"]
        assert set(receipt["required"]) == {"command", "exit_code", "snapshot_id"}
        assert receipt["properties"]["exit_code"]["type"] == "integer"
        decision = tools["assess_plan"]["inputSchema"]["$defs"]["PlanDecision"]
        assert decision["properties"]["decision"]["enum"] == ["adopt", "defer", "reject"]
        for receipt_input in (
            {"command": "pytest", "exit_status": 0, "fingerprint": "current"},
            {"command": "pytest", "exit_code": True, "snapshot_id": "current"},
        ):
            assert rpc(
                "tools/call",
                {"name": "validate_change", "arguments": {"receipts": [receipt_input]}},
            )["isError"]
        assert rpc(
            "tools/call",
            {
                "name": "assess_plan",
                "arguments": {
                    "task": "Improve owner",
                    "files": ["owner.py"],
                    "decisions": [{"proposal_id": "p", "decision": "ignore", "reason": "No"}],
                },
            },
        )["isError"]
        assert not app.calls
        result = rpc(
            "tools/call",
            {
                "name": "validate_change",
                "arguments": {
                    "receipts": [
                        {"command": "pytest", "exit_code": 0, "snapshot_id": "current"},
                    ]
                },
            },
        )
        assert not result.get("isError")
        assert app.calls[-1] == (
            "validate_change",
            ([{"command": "pytest", "exit_code": 0, "snapshot_id": "current"}], None),
        )


def test_http_exposes_expansion_and_rejects_malformed_typed_inputs():
    app = RecordingApplication()
    with TestClient(
        create_app(app), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    ) as client:
        assert (
            client.post("/api/validate", json={"receipts": [{"command": "pytest"}]}).status_code
            == 422
        )
        assert (
            client.post(
                "/api/assess",
                json={
                    "task": "Improve",
                    "files": ["owner.py"],
                    "decisions": [{"proposal_id": "p", "decision": "adopt"}],
                },
            ).status_code
            == 422
        )
        assert not app.calls
        assert (
            client.get(
                "/api/entity", params={"entity_id": "owner.py", "offset": 4, "full": True}
            ).status_code
            == 200
        )
        assert app.calls[-1] == ("inspect", ("owner.py", 30, 4, True))
        assert (
            client.post(
                "/api/assess",
                json={"task": "Improve", "files": ["owner.py"], "include_brief": True},
            ).status_code
            == 200
        )
        assert app.calls[-1] == ("assess_plan", ("Improve", ["owner.py"], None, True))
