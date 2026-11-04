"""Request 19: the same operations over HTTP, CLI and MCP stdio, plus simulation."""

import base64
import json
import subprocess

from conftest import CANDIDATE, expect_error, uid

OPERATIONS = {
    "get_assertion",
    "list_assertions",
    "materialize",
    "list_branches",
    "diff_branch",
    "query",
    "simulate",
    "export_bundle",
    "audit_graph",
}


def python():
    return str(CANDIDATE / ".venv" / "bin" / "python")


def cli(app, operation, args=None):
    argv = [python(), "-m", "chronoweave", "cli", operation]
    if args is not None:
        argv += ["--args", json.dumps(args)]
    done = subprocess.run(
        argv, cwd=CANDIDATE, env=app.env, capture_output=True, text=True, timeout=60
    )
    return done.returncode, json.loads(done.stdout)


class Mcp:
    def __init__(self, app):
        self.proc = subprocess.Popen(
            [python(), "-m", "chronoweave", "mcp"],
            cwd=CANDIDATE,
            env=app.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.next_id = 0

    def send(self, method, params=None, notify=False):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            self.next_id += 1
            message["id"] = self.next_id
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        reply = json.loads(self.proc.stdout.readline())
        assert reply.get("id") == self.next_id and reply.get("jsonrpc") == "2.0", reply
        return reply

    def tool(self, name, arguments):
        return self.send("tools/call", {"name": name, "arguments": arguments})["result"]

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(10)


def history(app):
    t = app.tenant()
    a = app.add("acct", 1, 10, tenant=t, id="a")
    app.add("limit", 2, 20, tenant=t, id="b", parents=["a"])
    assert app.retract("a", tenant=t).status_code == 200
    app.add("note", "n", 30, tenant=t, id="c")
    name = app.fork(tenant=t)
    app.add("limit", 9, 25, branch=name, tenant=t, id="h", parents=["c"])
    return t, name, a


def http_equivalents(app, t, name, a):
    """(operation, arguments, HTTP JSON body) triples."""
    get = lambda path, **params: app.get(path, t, params=params).json()  # noqa: E731
    return [
        ("get_assertion", {"id": "b"}, get("/api/branches/main/assertions/b")),
        (
            "get_assertion",
            {"id": "a", "asserted_at": a["asserted_at"]},
            get("/api/branches/main/assertions/a", asserted_at=a["asserted_at"]),
        ),
        ("get_assertion", {"id": "h", "branch": name}, get(f"/api/branches/{name}/assertions/h")),
        ("list_assertions", {"prefix": "l"}, get("/api/branches/main/assertions", prefix="l")),
        (
            "list_assertions",
            {"status": "tainted_pending_reconcile"},
            get("/api/branches/main/assertions", status="tainted_pending_reconcile"),
        ),
        ("materialize", {"effective_at": 25}, get("/api/branches/main/state", effective_at=25)),
        ("materialize", {"branch": name}, get(f"/api/branches/{name}/state")),
        ("list_branches", {}, get("/api/branches")),
        ("diff_branch", {"branch": name}, get(f"/api/branches/{name}/diff")),
        (
            "query",
            {"q": "WHERE TAINTED"},
            app.post("/api/query", t, json={"q": "WHERE TAINTED"}).json(),
        ),
        ("audit_graph", {}, get("/api/audit/graph")),
    ]


def test_cli_matches_http(app):
    t, name, a = history(app)
    cases = http_equivalents(app, t, name, a)
    for index in (1, 3, 6, 8, 9):  # a sample; MCP covers every operation
        operation, args, expected = cases[index]
        code, out = cli(app, operation, {**args, "tenant": t})
        assert code == 0 and out == expected, (operation, args, out)
    code, out = cli(app, "export_bundle", {"tenant": t, "effective_from": 0})
    http_bytes = app.get("/api/bundles/export", t, params={"effective_from": 0}).content
    assert code == 0 and base64.b64decode(out["bundle_base64"]) == http_bytes
    for operation, args, error in (
        ("get_assertion", {"id": "zzz", "tenant": t}, "not_found"),
        ("query", {"q": "WHERE", "tenant": t}, "query_syntax"),
        ("list_branches", {"tenant": uid("t-")}, "unknown_tenant"),
    ):
        code, out = cli(app, operation, args)
        assert code == 1 and out["error"]["code"] == error, (operation, out)


def test_mcp_matches_http(app):
    t, name, a = history(app)
    mcp = Mcp(app)
    try:
        init = mcp.send(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}},
        )
        assert init["result"]["protocolVersion"] == "2025-06-18"
        assert (
            init["result"]["serverInfo"]["name"] == "chronoweave"
            and "tools" in init["result"]["capabilities"]
        )
        assert mcp.send("notifications/initialized", notify=True) is None
        tools = mcp.send("tools/list")["result"]["tools"]
        assert OPERATIONS <= {tool["name"] for tool in tools}
        assert all(
            isinstance(tool["inputSchema"], dict) and tool.get("description") for tool in tools
        )
        for operation, args, expected in http_equivalents(app, t, name, a):
            result = mcp.tool(operation, {**args, "tenant": t})
            assert result["isError"] is False and result["structuredContent"] == expected, operation
            assert (
                json.loads(result["content"][0]["text"]) == expected
                and result["content"][0]["type"] == "text"
            )
        failed = mcp.tool("get_assertion", {"id": "zzz", "tenant": t})
        assert (
            failed["isError"] is True
            and failed["structuredContent"]["error"]["code"] == "not_found"
        )
        exported = mcp.tool("export_bundle", {"tenant": t})["structuredContent"]
        assert (
            base64.b64decode(exported["bundle_base64"]) == app.get("/api/bundles/export", t).content
        )
        assert (
            mcp.send("tools/call", {"name": "drop_everything", "arguments": {}})["error"]["code"]
            == -32602
        )
        assert mcp.send("no/such/method")["error"]["code"] == -32601
    finally:
        mcp.close()


def test_simulation_stores_nothing(app):
    t, name, a = history(app)
    before = (
        app.get("/api/branches", t).json(),
        app.get(f"/api/tenants/{t}").json()["usage"],
        app.get("/api/branches/main/assertions", t).json(),
    )
    frm = {"effective_at": 15, "asserted_at": app.record("c", tenant=t)["asserted_at"]}
    hypothetical = [
        {
            "id": "s1",
            "key": "limit",
            "value": 7,
            "effective_at": 20,
            "asserted_at": app.t(),
            "causal_parents": ["c"],
            "author": "x",
        },
        {
            "id": "s2",
            "key": "extra",
            "value": 1,
            "effective_at": 40,
            "asserted_at": app.t(),
            "causal_parents": ["s1"],
            "author": "x",
        },
    ]
    response = app.post(
        "/api/simulate", t, json={"from": frm, "assertions": hypothetical, "effective_at": 50}
    )
    assert response.status_code == 200, response.text
    simulated = response.json()
    real = app.fork(frm=frm, tenant=t)
    for item in hypothetical:
        assert app.post(f"/api/branches/{real}/assertions", t, json=item).status_code == 201
    expected = app.get(f"/api/branches/{real}/diff", t, params={"effective_at": 50}).json()
    assert simulated == {**expected, "branch": None}
    assert simulated["assertions"] == ["s1", "s2"]
    app.delete(f"/api/branches/{real}", t)
    after = (
        app.get("/api/branches", t).json(),
        app.get(f"/api/tenants/{t}").json()["usage"],
        app.get("/api/branches/main/assertions", t).json(),
    )
    assert [b for b in after[0]["branches"] if b["name"] != real] == before[0]["branches"]
    assert after[1] == before[1] and after[2] == before[2]
    bad = [{**hypothetical[0], "causal_parents": ["ghost"]}]
    expect_error(app.post("/api/simulate", t, json={"assertions": bad}), 422, "unknown_parent")
    early = [{**hypothetical[0], "effective_at": 1}]
    expect_error(
        app.post("/api/simulate", t, json={"from": frm, "assertions": early}), 422, "before_fork"
    )
    code, out = cli(
        app, "simulate", {"tenant": t, "from": frm, "assertions": hypothetical, "effective_at": 50}
    )
    assert code == 0 and out == simulated
