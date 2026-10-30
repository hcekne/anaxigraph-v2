"""Evaluator-owned behavioral probes and attribution, never imported by candidates.

This is process isolation, not an adversarial host sandbox. Use separate users or
containers for hostile candidates; public oracle source is not a held-out dataset.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

from .fixture import CHECK_ADAPTER, digest


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): __import__("hashlib")
        .sha256(path.read_bytes())
        .hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not path.is_symlink()
        and not any(
            part in {".git", ".anaxigraph-v2", "__pycache__"}
            for part in path.relative_to(root).parts
        )
        and path.name not in {"submission.json", "TASK.json"}
        and path.relative_to(root).as_posix() != "anaxigraph/record.json"
    }


def checks(spec):
    return ["gate"] + [
        prefix + name for name in spec["ids"]["providers"] for prefix in ("p:", "c:")
    ]


def token(spec, tenant="north", **options):
    return {"role": "sender", "tenant": tenant, "audience": "notifications", **options}


def normalize(spec, text):
    return (unicodedata.normalize("NFC", text) if spec["nfc"] else text).strip().upper()


def expect_error(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError("Required rejection did not occur")


def provider(root, spec, name):
    metadata = {
        path.stem: json.loads(path.read_text())
        for path in (root / "contracts/providers").glob("*.json")
    }
    if name not in spec["active"]:
        assert name not in metadata, "Retired capability remains advertised"
        assert not (root / f"providers/{name}.py").exists(), "Retired implementation remains"
        return 2
    module = importlib.import_module("providers." + name)
    expected_protocol = spec["capability_version"] if name == spec["ids"]["providers"][0] else 1
    assert metadata[name]["protocol"] == module.PROTOCOL == expected_protocol
    for text in (" hi ", " e\u0301 ", "Straße", "  lower \n", "   "):
        expected = normalize(spec, text)
        if name in spec["envelopes"]:
            expected = {"text": expected, "receipt": name}
        assert module.deliver(text) == expected, "Adapter behavioral contract changed"
    return 6


def consumer(root, spec, name, previous):
    execute = importlib.import_module("solution").execute
    index = spec["ids"]["providers"].index(name)
    clients = [
        value
        for n, value in enumerate(spec["ids"]["clients"])
        if n % len(spec["ids"]["providers"]) == index
    ]
    count = 0
    retained = None
    for client in clients:
        send = importlib.import_module("integrations." + client).send
        state, calls = {}, []
        request = {"action": "send", "tenant": "north", "id": "m", "text": " e\u0301 ", "now": 100}
        original = copy.deepcopy(request)

        def transport(channel, payload):
            calls.append((channel, copy.deepcopy(payload)))
            payload.clear()
            return True

        result = send(request, state, token(spec), transport)
        expected = {
            "ok": True,
            "id": "m",
            "text": normalize(spec, request["text"]),
            "provider": spec["routes"][client],
        }
        assert result == expected, "Consumer output/route contract"
        assert request == original
        assert calls == [
            (
                "send",
                {
                    "id": "m",
                    "text": expected["text"],
                    "tenant": "north",
                    "provider": expected["provider"],
                    "timeout_seconds": 2.0,
                },
            )
        ]
        result["text"] = "mutated by caller"
        restored = json.loads(json.dumps(state))
        calls.clear()
        assert send(original, restored, token(spec), transport) == expected
        assert not calls, "Restarted successful retry repeated a side effect"
        if spec["conflicts"]:
            expect_error(
                lambda: send(original | {"text": "different"}, restored, token(spec), transport)
            )
        before = copy.deepcopy(restored)
        expect_error(
            lambda: send(request | {"id": "bad", "text": ""}, restored, token(spec), transport)
        )
        expect_error(
            lambda: send(request | {"id": "bad"}, restored, token(spec, role="viewer"), transport)
        )
        if spec["tenant_auth"]:
            expect_error(
                lambda: send(
                    request | {"id": "bad"}, restored, token(spec, tenant="south"), transport
                )
            )
        if spec["expiry"]:
            expect_error(
                lambda: send(
                    request | {"id": "bad"}, restored, token(spec, expires_at=100), transport
                )
            )
        if spec["auth_audience"]:
            expect_error(
                lambda: send(
                    request | {"id": "bad"}, restored, token(spec, audience="other"), transport
                )
            )
        if "operator" in spec["roles"]:
            assert send(request, restored, token(spec, role="operator"), transport) == expected
        assert restored == before and not calls, "Rejected requests changed state or effects"
        long = "é" * 40
        if spec["bytes"]:
            expect_error(
                lambda: send(request | {"id": "budget", "text": long}, {}, token(spec), transport)
            )
        else:
            assert send(
                request | {"id": "budget", "text": long}, {}, token(spec), lambda *args: True
            )["ok"]
        failed_state = {}
        assert send(request, failed_state, token(spec), lambda *args: False) == {
            "ok": False,
            "error": "unavailable",
        }
        assert failed_state == {}, "Failed send committed state"
        for failure in (False, OSError("offline"), RuntimeError("unexpected")):
            populated = copy.deepcopy(before)
            untouched = copy.deepcopy(populated)
            observed = []

            def unavailable(channel, payload):
                observed.append((channel, copy.deepcopy(payload)))
                if isinstance(failure, Exception):
                    raise failure
                return failure

            try:
                rejected = send(
                    request | {"id": "new-failure"}, populated, token(spec), unavailable
                )
                assert not isinstance(failure, RuntimeError), (
                    "Unexpected transport exception swallowed"
                )
                assert rejected == {"ok": False, "error": "unavailable"}
            except RuntimeError:
                assert isinstance(failure, RuntimeError)
            assert populated == untouched, "Failed send destroyed existing receipts or events"
            assert len(observed) == 1 and observed[0][0] == "send"
        if spec["strict_ack"]:
            assert send(request, {}, token(spec), lambda *args: {"ok": False})["ok"] is False
            assert send(request, {}, token(spec), lambda *args: 1)["ok"] is False
            assert send(request, {}, token(spec), lambda *args: {"ok": True})["ok"] is True
        if spec["fallback"]:
            route = root / f"config/routes/{client}.json"
            original_route = route.read_text()
            try:
                route.write_text("{}")
                fallback = send(request | {"id": "fallback"}, {}, token(spec), lambda *args: True)
                assert fallback["provider"] == spec["ids"]["providers"][5], (
                    "Registry fallback ignored"
                )
            finally:
                route.write_text(original_route)
        count += 12
        # Keep an old unsent event, exercise failure/retry, then exactly-once confirmation.
        flush = {"action": "flush", "tenant": "north"}
        calls.clear()
        attempts = []

        def failing(channel, payload):
            attempts.append((channel, copy.deepcopy(payload)))
            return False

        for _ in range(4):
            assert execute(client, flush, restored, token(spec), failing)["pending"] == 1
        assert len(attempts) == (3 if spec["storage_version"] >= 3 else 4)
        envelope = {"id": "m", "text": expected["text"], "version": spec["event_version"]}
        if spec["event_version"] >= 2:
            envelope["tenant"] = "north"
        assert attempts[0] == ("event", envelope), "Event wire schema/old payload contract"
        if spec["event_replay"]:
            assert execute(
                client, {"action": "replay", "tenant": "north"}, restored, token(spec), transport
            ) == {"ok": True}
        if spec["storage_version"] < 3 or spec["event_replay"]:
            assert execute(client, flush, restored, token(spec), transport) == {
                "ok": True,
                "sent": 1,
                "pending": 0,
            }
            calls.clear()
            assert execute(client, flush, restored, token(spec), transport)["sent"] == 0
            assert not calls, "Already confirmed event was replayed"
        count += 8
        if client == spec["ids"]["clients"][0]:
            opaque = copy.deepcopy(previous.get("state", {})) if previous else {}
            history = copy.deepcopy(previous.get("history", [])) if previous else []
            no_effects = []
            for prior in history:
                assert (
                    execute(
                        client,
                        prior["request"],
                        opaque,
                        token(spec),
                        lambda *args: no_effects.append(args),
                    )
                    == prior["result"]
                )
            assert not no_effects, "Prior-stage state lost its successful receipts"
            new = {
                "action": "send",
                "tenant": "north",
                "id": f"history-{spec['stage']}-{len(history)}",
                "text": "earlier message",
            }
            historical = execute(client, new, opaque, token(spec), lambda *args: True)
            assert historical["ok"]
            history.append({"request": new, "result": copy.deepcopy(historical)})
            pending_copy = copy.deepcopy(opaque)
            old_events = []
            execute(
                client,
                {"action": "flush", "tenant": "north"},
                pending_copy,
                token(spec),
                lambda channel, payload: old_events.append(copy.deepcopy(payload)) or True,
            )
            assert len(old_events) == len(history), "Earlier pending events were lost or duplicated"
            assert all(event["version"] == spec["event_version"] for event in old_events)
            assert [event["id"] for event in old_events] == [
                item["request"]["id"] for item in history
            ]
            retained = {"state": json.loads(json.dumps(opaque)), "history": history}
            count += len(history)
    return count, retained


def gate(root, spec):
    policy = json.loads((root / "anaxigraph/ci-policy.json").read_text())
    assert "gate" in policy.get("always", []), "Required gate removed"
    assert {row["id"] for row in policy["targets"]} == set(checks(spec)), (
        "Existing checks removed or renamed"
    )
    assert all(row["command"] == "python3 checks.py " + row["id"] for row in policy["targets"]), (
        "Verification command identity changed"
    )
    assert (root / "checks.py").read_text() == CHECK_ADAPTER, "Trusted check adapter changed"
    assert not any(path.is_symlink() for path in root.rglob("*")), (
        "Symlinked inputs are unsupported, not silently fingerprinted"
    )
    assert not list(root.rglob("*.so")), "Non-stdlib native extension"
    return 6


def independence(root, spec):
    """Run public adapter contracts with every neighboring implementation unavailable."""
    result = {}
    for name in spec["active"]:
        code = """import importlib.abc, json, sys
from pathlib import Path
root, allowed = sys.argv[1:]
class BlockNeighbors(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith('providers.') and fullname != 'providers.' + allowed:
            raise ImportError('neighbor implementation unavailable')
sys.meta_path.insert(0, BlockNeighbors())
sys.path.insert(0, root)
from importlib import import_module
reply = import_module('providers.' + allowed).deliver(' independent ')
assert (reply['text'] if isinstance(reply, dict) else reply) == 'INDEPENDENT'
"""
        reply = subprocess.run(
            [sys.executable, "-c", code, str(root.resolve()), name],
            capture_output=True,
            text=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            timeout=20,
        )
        result[name] = {"passed": reply.returncode == 0, "stderr": reply.stderr[-300:]}
    return result


def declared_run(root, spec, selected, previous=None):
    """Execute the policy's fixed checker capabilities, with external packet binding.

    Unknown commands are rejected, never executed as arbitrary shell. The adapter
    bytes and commands are part of gate and candidate fingerprints.
    """
    policy = json.loads((root / "anaxigraph/ci-policy.json").read_text())
    commands = {row["id"]: row["command"] for row in policy["targets"]}
    if (root / "checks.py").read_text() != CHECK_ADAPTER or any(
        commands.get(key) != "python3 checks.py " + key for key in selected
    ):
        return {
            "checks": {
                key: {"passed": False, "error": "untrusted_verification_command"}
                for key in selected
            },
            "retained": None,
            "unchanged": True,
        }
    before, rows, retained = snapshot(root), {}, None
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="drift-declared-") as temporary:
        packet = Path(temporary) / "packet.json"
        packet.write_text(json.dumps({"spec": spec, "previous": previous}))
        environment = dict(
            os.environ,
            PYTHONDONTWRITEBYTECODE="1",
            DRIFT_EVALUATOR_ROOT=str(Path(__file__).resolve().parents[2]),
            DRIFT_ORACLE_PACKET=str(packet),
        )
        for check in selected:
            command = [sys.executable, "-I", "-B", "checks.py", check]
            reply = subprocess.run(
                command, cwd=root, env=environment, capture_output=True, text=True, timeout=30
            )
            try:
                result = json.loads(reply.stdout)
                row = result["checks"][check]
                if reply.returncode and row["passed"]:
                    row = {"passed": False, "error": "contradictory_exit"}
                retained = result.get("retained") or retained
            except (ValueError, KeyError):
                row = {
                    "passed": False,
                    "error": "missing_execution_receipt",
                    "stderr": reply.stderr[-500:],
                }
            rows[check] = row | {"command": command, "exit_code": reply.returncode}
    return {
        "checks": rows,
        "retained": retained,
        "unchanged": before == snapshot(root),
        "candidate_digest": digest(before),
        "elapsed_seconds": round(time.perf_counter() - started, 6),
    }


def worker(root, spec, selected, previous):
    sys.path.insert(0, str(root))
    rows, retained = {}, None
    for check in selected:
        started = time.perf_counter()
        try:
            if check == "gate":
                assertions = gate(root, spec)
            elif check.startswith("p:"):
                assertions = provider(root, spec, check[2:])
            else:
                assertions, state = consumer(root, spec, check[2:], previous)
                retained = state or retained
            rows[check] = {"passed": True, "assertions": assertions}
        except Exception as error:
            rows[check] = {
                "passed": False,
                "error": type(error).__name__,
                "message": str(error)[:500],
            }
        rows[check]["elapsed_seconds"] = round(time.perf_counter() - started, 6)
    return {"checks": rows, "retained": retained}


def run(root, spec, selected, previous=None):
    if len(set(selected)) != len(selected) or not set(selected) <= set(checks(spec)):
        raise ValueError("Unknown or repeated oracle checks")
    before = snapshot(root)
    with tempfile.TemporaryDirectory(prefix="drift-oracle-") as temporary:
        packet = Path(temporary) / "packet.json"
        packet.write_text(json.dumps({"spec": spec, "selected": selected, "previous": previous}))
        command = [
            sys.executable,
            "-m",
            "experiments.drift.oracle",
            "--worker",
            str(root.resolve()),
            "--packet",
            str(packet),
        ]
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        started = time.perf_counter()
        reply = subprocess.run(
            command,
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
        )
    if reply.returncode:
        return {
            "checks": {
                key: {"passed": False, "error": "oracle_process_failed"} for key in selected
            },
            "retained": None,
            "stderr": reply.stderr[-1000:],
            "command": command[:-1] + ["<private-packet>"],
            "unchanged": before == snapshot(root),
        }
    result = json.loads(reply.stdout)
    result.update(
        command=command[:-1] + ["<private-packet>"],
        elapsed_seconds=round(time.perf_counter() - started, 6),
        unchanged=before == snapshot(root),
        candidate_digest=digest(before),
    )
    return result


def mutate(root, spec):
    """Concrete fault in the affected implementation, not an oracle expected-set label."""
    stage, p = spec["stage"], spec["ids"]["providers"]
    patches = {}
    if stage in {0, 1, 9, 12, 20, 24, 25}:
        name = (
            p[1]
            if stage == 9
            else p[4]
            if stage == 20
            else p[-2]
            if stage == 24
            else p[-1]
            if stage == 25
            else p[0]
        )
        patches[f"providers/{name}.py"] = (
            "\ndef _normalize(text): return 'CORRUPT'\ndef deliver(text): return 'CORRUPT'\n"
        )
    elif stage in {8, 18}:
        patches.update(
            {
                f"providers/{name}.py": "\ndef deliver(text): return 'CORRUPT'\n"
                for name in spec["active"]
            }
        )
    elif stage in {3, 13, 17}:
        if stage == 17:
            from .fixture import specification

            old = specification(16, spec["ids"])
            clients = [client for client, provider in old["routes"].items() if provider == p[2]]
            patches[f"providers/{p[2]}.py"] = "def deliver(text): return text\n"
        else:
            clients = [spec["ids"]["clients"][1 if stage == 3 else 0]]
        patches["solution.py"] = (
            f"\n_original_execute = execute\ndef execute(client, request, state, token, transport=None):\n    if client in {clients!r} and request.get('action') == 'send': return {{'ok': False, 'error': 'regression'}}\n    return _original_execute(client, request, state, token, transport)\n"
        )
    else:
        patches["solution.py"] = (
            "\n_original_execute = execute\ndef execute(client, request, state, token, transport=None):\n    if request.get('action') == 'send': return {'ok': False, 'error': 'regression'}\n    return _original_execute(client, request, state, token, transport)\n"
        )
    backups = {}
    for relative, suffix in patches.items():
        path = root / relative
        backups[path] = path.read_text() if path.exists() else None
        path.write_text((backups[path] or "") + suffix)
    return backups


def submission(value, spec):
    if not isinstance(value, dict) or set(value) != {"selected", "deploy", "unknowns"}:
        raise ValueError("Submission must contain exactly selected, deploy, unknowns")
    for key in value:
        items = value[key]
        if (
            not isinstance(items, list)
            or len(items) > 1000
            or any(not isinstance(item, str) or not item or len(item) > 1000 for item in items)
            or len(set(items)) != len(items)
        ):
            raise ValueError("Submission lists must be bounded, unique nonempty strings")
    if set(value["selected"]) - set(checks(spec)) or set(value["deploy"]) - (
        set(spec["ids"]["providers"]) | {"host"}
    ):
        raise ValueError("Submission names unknown targets or artifacts")
    return copy.deepcopy(value)


def evaluate(root, spec, selected, plan, previous=None, *, evidence=None):
    """Safety first. No count/cost efficiency credit survives a failed gate."""
    full = run(root, spec, checks(spec), previous)
    executed = declared_run(root, spec, selected, previous)
    backups = mutate(root, spec)
    try:
        mutant = run(root, spec, checks(spec), previous)
        selected_mutant = declared_run(root, spec, selected, previous)
    finally:
        for path, original in backups.items():
            if original is None:
                path.unlink()
            else:
                path.write_text(original)
    required = {key for key, row in mutant["checks"].items() if not row["passed"]} | {"gate"}
    baseline_ok = all(row["passed"] for row in full["checks"].values())
    failures = []
    if not baseline_ok:
        failures.append("behavioral_regression")
    if not full["unchanged"] or not executed["unchanged"] or not mutant["unchanged"]:
        failures.append("verification_modified_candidate")
    if not any(not row["passed"] for row in mutant["checks"].values()):
        failures.append("invalid_fail_full_mutant")
    if not any(not row["passed"] for row in selected_mutant["checks"].values()):
        failures.append("selected_verification_missed_mutant")
    missing = sorted(required - set(selected))
    if missing:
        failures.append("missing_required_checks")
    planned_ids = {row["id"] for row in plan.get("selected_targets", [])}
    if required - planned_ids:
        failures.append("planner_missing_required_checks")
    if planned_ids - set(selected):
        failures.append("omitted_planned_obligation")
    if (
        any(not row["passed"] for row in executed["checks"].values())
        or "gate" not in executed["checks"]
    ):
        failures.append("failed_or_omitted_gate")
    if (
        plan.get("status") != "planned"
        or not plan.get("execution_ready")
        or plan.get("uncovered_paths")
    ):
        failures.append("unresolved_scope")
    current = digest(snapshot(root))
    if evidence is not None and (
        evidence.get("candidate_digest") != current or evidence.get("check_ids") != sorted(selected)
    ):
        failures.append("stale_evidence")
    safe = not failures
    independent = independence(root, spec)
    if any(not row["passed"] for row in independent.values()):
        failures.append("adapter_not_independently_testable")
        safe = False
    return {
        "qualified": safe,
        "hard_failures": failures,
        "required_checks": sorted(required),
        "missing_checks": missing,
        "full": full,
        "selected_execution": executed,
        "fail_full_mutant": mutant,
        "selected_mutant_execution": selected_mutant,
        "retained": full["retained"],
        "adapter_independence": independent,
        "observed_work": {
            "selected_check_count": len(executed["checks"]),
            "full_check_count": len(checks(spec)),
            "selected_elapsed_seconds": executed.get("elapsed_seconds"),
            "basis": "Actual local evaluator subprocess; not production CI timings",
        },
        "synthetic_cost_model": {
            "unit": "equal check units; excludes real runner setup",
            "selected_units": len(selected),
            "full_units": len(checks(spec)),
            "efficiency_credit": (len(checks(spec)) - len(selected)) if safe else None,
        },
        "assurance": "Bounded public oracle probes; not universal equivalence or adversarial sandbox attestation",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    args = parser.parse_args()
    packet = json.loads(args.packet.read_text())
    print(json.dumps(worker(args.worker, packet["spec"], packet["selected"], packet["previous"])))


if __name__ == "__main__":
    main()
