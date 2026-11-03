"""Exercise the real Notes oracle against controlled outbound-call behavior."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


def platform_oracle():
    source = Path(__file__).parent / "scenario" / "hidden" / "probe_2.py"
    functions = [
        node
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name in {"invoke", "test_platform_rules"}
    ]
    namespace = {
        "error_code": lambda response: response.code,
        "tool_calls": lambda hub, integration, tool: hub.calls,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
    return namespace["test_platform_rules"]


class Candidate:
    def __init__(
        self, hub, *, health_calls=0, quota_leak=False, disabled_leak=False, allowed_calls=1
    ):
        self.hub, self.health_calls = hub, health_calls
        self.quota_leak, self.disabled_leak = quota_leak, disabled_leak
        self.allowed_calls = allowed_calls
        self.configured, self.disabled, self.used = False, False, 0

    @staticmethod
    def response(status, body=None, code=None):
        return SimpleNamespace(status_code=status, json=lambda: body, text=str(body), code=code)

    def workspace(self):
        return "workspace"

    def creds(self, integration, ws):
        self.configured = True

    def get(self, path, ws):
        if self.configured:
            self.hub.calls.extend(["health"] * self.health_calls)
        return self.response(
            200, {"kind": "mcp", "status": "ok" if self.configured else "unconfigured"}
        )

    def settings(self, ws, **values):
        self.disabled = bool(values.get("disabled_integrations"))

    def post(self, path, ws, json):
        if self.disabled:
            if self.disabled_leak:
                self.hub.calls.append("disabled invocation leaked")
            return self.response(403, code="integration_disabled")
        if self.used:
            if self.quota_leak:
                self.hub.calls.append("quota invocation leaked")
            return self.response(429, code="quota_exceeded")
        self.used += 1
        self.hub.calls.extend(["allowed invocation"] * self.allowed_calls)
        return self.response(200)

    def audit(self, ws):
        return [{"integration": "notes"}]


@pytest.mark.parametrize("health_calls", [0, 1, 2])
def test_notes_platform_oracle_accepts_health_calls_before_quota_invocations(health_calls):
    hub = SimpleNamespace(calls=[])
    platform_oracle()(Candidate(hub, health_calls=health_calls), hub)
    assert hub.calls == ["health"] * health_calls + ["allowed invocation"]


@pytest.mark.parametrize("leak", ["quota_leak", "disabled_leak"])
def test_notes_platform_oracle_rejects_outbound_calls_from_rejected_invocations(leak):
    hub = SimpleNamespace(calls=[])
    with pytest.raises(AssertionError):
        platform_oracle()(Candidate(hub, health_calls=1, **{leak: True}), hub)


@pytest.mark.parametrize("allowed_calls", [0, 2])
def test_notes_platform_oracle_requires_exactly_one_call_for_the_allowed_invocation(allowed_calls):
    hub = SimpleNamespace(calls=[])
    with pytest.raises(AssertionError):
        platform_oracle()(Candidate(hub, health_calls=1, allowed_calls=allowed_calls), hub)
