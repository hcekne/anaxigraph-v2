"""Request 19: CLI and MCP (stdio JSON-RPC) share state and results with the HTTP API."""

import json

from conftest import DEFAULT_BOUNDARY, assert_cells, lm

START = {(0, 0): 6.0, (2, -2): 1.5, (-1, 2): 0.75}
TOOLS = {"get_field", "step_field", "list_dialects", "quarantine_status", "audit_field"}


def prepared(app):
    fid = app.field(radius=3, k=0.125)
    app.set_cells(fid, START)
    app.transducer(fid, "bad", [(-3, 3)])
    for _ in range(3):
        app.frame(fid, "bad", [((-3, 3), 1.0)])
    model = lm.Field(3, k=0.125, boundary=DEFAULT_BOUNDARY)
    model.set(START)
    return fid, model


def cli_json(app, *args):
    result = app.cli(*args)
    return result.returncode, json.loads(
        result.stdout.strip().splitlines()[-1]
    ) if result.stdout.strip() else None


def call(name, arguments, ident):
    return {
        "jsonrpc": "2.0",
        "id": ident,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def test_cli_steps_the_stored_field(fresh_app):
    app = fresh_app
    fid, model = prepared(app)
    app.stop()
    code, body = cli_json(app, "step", "--field", fid, "--ticks", "3")
    assert code == 0, body
    model.step(3)
    assert body["tick"] == 3 and body["total"] == model.total()
    code, audit = cli_json(app, "audit", "--field", fid)
    assert code == 0 and audit["ok"] is True
    code, quarantine = cli_json(app, "quarantine", "--field", fid)
    assert quarantine == {"quarantined": ["bad"], "pending": []}
    code, dialects = cli_json(app, "dialects")
    code, summary = cli_json(app, "field", "--field", fid)
    app.start()
    assert app.summary(fid) == summary == body
    assert app.audit(fid) == audit
    assert app.get("/api/dialects").json() == dialects
    assert_cells(app.cells(fid), model.v)


def test_cli_errors_use_the_envelope(fresh_app):
    app = fresh_app
    fid = app.field(radius=1)
    app.stop()
    code, body = cli_json(app, "step", "--field", "missing", "--ticks", "1")
    assert code == 1 and body["error"]["code"] == "not_found"
    code, body = cli_json(app, "step", "--field", fid, "--ticks", "0")
    assert code == 1 and body["error"]["code"] == "invalid_request"
    code, body = cli_json(app, "step", "--field", fid, "--ticks", "1", "--sub-ticks", "1")
    assert code == 1 and body["error"]["code"] == "invalid_request"
    code, body = cli_json(app, "step", "--field", fid, "--sub-ticks", "2")
    assert code == 0 and (body["tick"], body["sub_tick"]) == (0, 2)
    app.start()


def test_mcp_tools_match_http(fresh_app):
    app = fresh_app
    fid, model = prepared(app)
    app.step(fid)
    app.stop()
    replies = app.mcp(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            call("step_field", {"field": fid, "sub_ticks": 6}, 3),
            call("audit_field", {"field": fid}, 4),
            call("quarantine_status", {"field": fid}, 5),
            call("list_dialects", {}, 6),
            call("get_field", {"field": fid}, 7),
        ]
    )
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, 6, 7]
    init = replies[0]["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["serverInfo"]["name"] == "aetherloom"
    tools = replies[1]["result"]["tools"]
    assert TOOLS <= {t["name"] for t in tools}
    assert all(isinstance(t["inputSchema"], dict) for t in tools)
    results = {}
    for reply in replies[2:]:
        result = reply["result"]
        assert result["isError"] is False
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        results[reply["id"]] = result["structuredContent"]
    model.step(1)
    model.run(6)
    assert (results[3]["tick"], results[3]["sub_tick"]) == (2, 2)
    app.start()
    assert app.summary(fid) == results[7] == results[3]
    assert app.audit(fid) == results[4]
    assert app.get(f"/api/fields/{fid}/quarantine").json() == results[5]
    assert app.get("/api/dialects").json() == results[6]
    assert_cells(app.cells(fid), model.v)


def test_mcp_errors(fresh_app):
    app = fresh_app
    fid = app.field(radius=1)
    app.stop()
    replies = app.mcp(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
            },
            call("get_field", {"field": "missing"}, 2),
            call("step_field", {"field": fid, "ticks": 5000}, 3),
            call("explode", {}, 4),
            {"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
        ]
    )
    by_id = {r["id"]: r for r in replies}
    for ident, code in ((2, "not_found"), (3, "invalid_request")):
        result = by_id[ident]["result"]
        assert result["isError"] is True and result["structuredContent"]["error"]["code"] == code
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert by_id[4]["error"]["code"] == -32602
    assert by_id[5]["error"]["code"] == -32601
    garbage = app.mcp([])  # nothing in, nothing out
    assert garbage == []
    app.start()
    assert app.summary(fid)["tick"] == 0


def test_same_inputs_same_bits_through_every_door(fresh_app):
    app = fresh_app
    a, model = prepared(app)
    b, _ = prepared(app)
    app.step(a, ticks=4)
    app.stop()
    replies = app.mcp([call("step_field", {"field": b, "ticks": 2}, 1)])
    assert replies[0]["result"]["isError"] is False
    code, _ = cli_json(app, "step", "--field", b, "--ticks", "2")
    assert code == 0
    app.start()
    assert app.cells(a) == app.cells(b)
    audit_a, audit_b = app.audit(a), app.audit(b)
    assert audit_a == audit_b
