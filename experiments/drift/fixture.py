"""Generated public notification product and sequential issues, not oracle answers.

Reference construction is evaluator infrastructure. Only the current generated tree
and current/earlier public issues enter a coding checkout; future issues stay outside.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

COUNT = 26

# This adapter is an executable verification capability, not candidate-authored
# test coverage. The coordinator binds an external packet only during grading.
CHECK_ADAPTER = """import json
import os
import sys
from pathlib import Path

if __name__ == '__main__':
    packet_path = os.environ.get('DRIFT_ORACLE_PACKET')
    if packet_path:
        sys.path.insert(0, os.environ['DRIFT_EVALUATOR_ROOT'])
        from experiments.drift.oracle import worker
        packet = json.loads(Path(packet_path).read_text())
        result = worker(Path.cwd(), packet['spec'], [sys.argv[1]], packet['previous'])
        print(json.dumps(result))
        raise SystemExit(int(not result['checks'][sys.argv[1]]['passed']))
    # Local public smoke is explicitly weaker than external contract evaluation.
    from importlib import import_module
    kind, _, name = sys.argv[1].partition(':')
    if kind == 'p' and (Path('providers') / (name + '.py')).exists():
        module = import_module('providers.' + name)
        reply = module.deliver(' sample ')
        assert (reply['text'] if isinstance(reply, dict) else reply) == 'SAMPLE'
    elif kind == 'c':
        assert callable(import_module('solution').execute)
    elif kind not in ('gate', 'p'):
        raise ValueError('unknown check')
    print('Public smoke passed; external contract checks were not run')
"""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def identities(components=12, integrations=48, seed=7103):
    if type(components) is not int or not 8 <= components <= 300:
        raise ValueError("components must be 8..300")
    if type(integrations) is not int or not components <= integrations <= 10_000:
        raise ValueError("integrations must be components..10000")
    rng = random.Random(seed)
    providers = [f"adapter_{rng.getrandbits(32):08x}" for _ in range(components)]
    clients = [f"client_{rng.getrandbits(40):010x}" for _ in range(integrations)]
    return {"providers": providers, "clients": clients, "seed": seed}


def specification(stage, ids):
    if type(stage) is not int or not -1 <= stage < COUNT:
        raise ValueError("stage must be -1..25")
    providers, clients = ids["providers"], ids["clients"]
    initial = providers[:-2]
    routes = {key: initial[i % len(initial)] for i, key in enumerate(clients)}
    if stage >= 3:
        routes[clients[1]] = providers[2]
    if stage >= 13:
        routes[clients[0]] = providers[3]
    active = list(initial)
    if stage >= 24:
        active.append(providers[-2])
        routes[clients[-2]] = providers[-2]
    if stage >= 25:
        active.append(providers[-1])
        routes[clients[-1]] = providers[-1]
    if stage >= 17:
        routes = {
            key: providers[3] if value == providers[2] else value for key, value in routes.items()
        }
        active.remove(providers[2])
    return {
        "stage": stage,
        "ids": ids,
        "routes": routes,
        "active": active,
        "envelopes": ([providers[0]] if stage >= 1 else [])
        + ([providers[-2]] if stage >= 24 else []),
        "tenant_auth": stage >= 2,
        "bytes": stage >= 4,
        "event_version": 2 if stage >= 5 else 1,
        "storage_version": 3 if stage >= 16 else 2 if stage >= 6 else 1,
        "seconds": stage >= 7,
        "nfc": stage >= 8,
        "conflicts": stage >= 10,
        "strict_ack": stage >= 11,
        "capability_version": 2 if stage >= 12 else 1,
        "roles": ["sender", "operator"] if stage >= 14 else ["sender"],
        "expiry": stage >= 15,
        "retry_limit": 3 if stage >= 16 else 100,
        "platform_version": 2 if stage >= 18 else 1,
        "auth_audience": "notifications" if stage >= 19 else None,
        "event_replay": stage >= 21,
        "fallback": stage >= 22,
        "rollout": stage >= 23,
    }


def issue(stage, ids):
    p, c = ids["providers"], ids["clients"]
    issues = [
        f"Improve private normalization in {p[0]} without changing its observable contract. "
        "The other adapters must remain independently usable.",
        f"The API adapter {p[0]} now returns {{text:<reply>,receipt:<adapter id>}} instead of "
        "a string. Migrate its consumer while preserving the public send result; MCP adapters stay strings.",
        "Require the token tenant to equal the send request tenant. Deny cross-tenant sends before effects.",
        f"Route {c[1]} dynamically to {p[2]}; preserve historical successful retries from its old route.",
        "The 64-unit input budget now counts UTF-8 bytes rather than Unicode characters. Reject excess before effects.",
        "Publish version 2 confirmation events with a tenant field; use current envelope version on pending events too.",
        "Move persisted receipts to version 2 records while keeping opaque version 1 saved states and retries usable.",
        "Runtime delivery timeout changes from 2000 milliseconds to 2 seconds. Transport payloads must still express 2.0 seconds.",
        "All adapters must normalize text to Unicode NFC before trimming and uppercasing; update shared codec ownership.",
        f"Optimize the private MCP normalization in {p[1]} while preserving behavior and accounting for any real callers.",
        "Reject reuse of a successful request ID with different text or tenant; same-input retries remain side-effect free.",
        "Transport acknowledgements may be a boolean or {ok:bool}. Only literal True or {ok:True} confirms delivery.",
        f"Publish protocol version 2 for {p[0]} in capability metadata; negotiate that version without changing other providers.",
        f"Move {c[0]} to {p[3]} without rewriting old successful results, outbox identities or receipts.",
        "Accept the operator role in addition to sender; other roles remain denied. Both transports use the shared auth policy.",
        "Reject tokens with expires_at <= request now (both integer seconds), before effects. Missing expiry remains accepted.",
        "Persist failed event attempt counts in storage version 3. Stop attempting an event after three failed acknowledgements; keep it pending.",
        f"Retire {p[2]}: move its routes to {p[3]}, remove its capability and implementation, and preserve prior serialized state.",
        "Upgrade the shared codec platform manifest to version 2, preserving behavior. Invalidate all actual toolchain consumers.",
        "Require token audience notifications. Reject absent/wrong audiences before any delivery; retain tenant, role and expiry protections.",
        f"Improve the private implementation of {p[4]} while preserving behavior and independent adapter verification.",
        "Add replay action: reset failed-attempt counters for pending events only. Already sent events must never be sent again.",
        f"If a client is absent from the runtime registry, use {p[5]} as the declared fallback. Unknown clients still fail.",
        "Prepare a release with verified immutable artifact identities and a rollback to the exact previous artifact. "
        "State from the previous release must remain usable. Write release.json documenting the new format version 2.",
        f"Add API adapter {p[-2]} through the existing delivery capability. It returns the same envelope as {p[0]}, with its own receipt. Route {c[-2]} to it; verify independent deployment and existing adapters unchanged.",
        f"Add MCP adapter {p[-1]} through the existing delivery capability with string replies. Route {c[-1]} to it; API implementations and unrelated integrations must remain independently usable.",
    ]
    return issues[stage]


def brief(ids):
    return (
        """Maintain a multi-integration notification router using stdlib only. Preserve all prior
issue requirements. No component count, extraction, service, class or preferred source layout is
required. Ordinary cohesive functions and a modular monolith are valid. Document important public
behavior and actual verification ownership. Use the existing declared-input AnaxiGraph CI planner;
do not implement a second selector or delete existing checks to improve selection.

Public operations: solution.execute(client_id, request, state, token, transport=None) -> dict.
State is caller-owned JSON-serializable data, initially {}, retained across issues and restarts.
Its representation is private. Separate dictionaries must stay independent. Do not mutate request,
token, transport payload aliases or returned receipt aliases into retained state.
send requests have action='send', nonempty id/text/tenant and optional integer now (default 100).
Baseline input budget is 64 characters; send role is sender. Reject invalid input/authorization
with ValueError before state changes or transport calls. Successful text is stripped and uppercase.
The runtime registry chooses a configured adapter; each adapter's deliver(text) is also independently
callable. API/MCP implementations must not import each other's private helpers. Publish adapter protocol metadata and consume the version actually advertised. Default transport
accepts. For a new send call transport('send', {id,text,tenant,provider,timeout_seconds}) once; timeout
is expressed in seconds, initially 2.0. False/OSError returns {ok:false,error:'unavailable'} with no
state change; unexpected exceptions propagate. Success returns {ok:true,id,text,provider} and queues
one event. A repeat successful ID returns the original result with no additional effect, including
after restart and route changes. Baseline IDs are tenant-qualified.
flush requests have action='flush' and tenant; attempt unsent events for that tenant in creation
order. transport('event', {id,text,version,...}) acknowledges an event. Preserve failed events and
never resend confirmed events. Return {ok:true,sent:<this call>,pending:<tenant pending count>}.
Current event envelope settings apply to pending events. Retrying delivery never sends a new message.
Provider mutation of input payloads must not corrupt saved state. Failed attempts before any success
must leave state unchanged, except the explicitly introduced later event attempt-counter feature.

anaxigraph/ci-policy.json declares check ownership. Check targets p:<adapter>, c:<adapter> and gate
are stable evaluator capabilities; they do not prescribe internal layout. Consumer checks cover
the integrations originally assigned to that adapter, including future route changes. Retain gate.
Changes to shared config/schema/auth/events/state/runtime/toolchain may widen verification even
with unchanged signatures. Unknown ownership requires the established broad fallback. You may
change policy declarations when supported by real inputs; the independent evaluator verifies them.
Write submission.json with exactly {selected:[target IDs],deploy:[artifact IDs],unknowns:[strings]}.
Artifacts are separately built adapter packages (IDs are adapter IDs) and the host runtime
(ID host). Registration/configuration updates can require a host rollout without rebuilding unrelated adapters.
These are proposals; the evaluator runs checks independently and attributes evidence to actual
candidate inputs. Never claim unrun results. Artifact/recovery assertions are separately checked.
No commits, pushes, external services or reading evaluator files outside this checkout.
"""
        + "\nKnown clients/adapters:\n"
        + json.dumps(ids)
    )


def engine(spec):
    """Compile only currently introduced requirements; no future solutions in seed."""
    receipt_key = "records" if spec["storage_version"] >= 2 else "receipts"
    receipt_read = (
        "state.get('records', state.get('receipts', {}))"
        if spec["storage_version"] >= 2
        else "state.get('receipts', {})"
    )
    migrations = ""
    if spec["storage_version"] >= 2:
        migrations += "        state['records'] = state.pop('receipts', state.get('records', {}))\n        state['version'] = 2\n"
    if spec["storage_version"] >= 3:
        migrations += "        state['version'] = 3\n"
    auth = ""
    if spec["tenant_auth"]:
        auth += "    if token.get('tenant') != tenant: raise ValueError('tenant')\n"
    if spec["expiry"]:
        auth += "    if 'expires_at' in token and token['expires_at'] <= request.get('now', 100): raise ValueError('expired')\n"
    if spec["auth_audience"]:
        auth += "    if token.get('audience') != auth['audience']: raise ValueError('audience')\n"
    limit = "len(text.encode('utf-8'))" if spec["bytes"] else "len(text)"
    normalized = "raw['text'] if isinstance(raw, dict) else raw" if spec["envelopes"] else "raw"
    timeout = "runtime['timeout']" if spec["seconds"] else "runtime['timeout'] / 1000"
    acknowledge = (
        "return value is True or isinstance(value, dict) and value.get('ok') is True"
        if spec["strict_ack"]
        else "return bool(value)"
    )
    conflict = (
        "            if old['input'] != [text, tenant]: raise ValueError('conflicting retry')\n"
        if spec["conflicts"]
        else ""
    )
    attempts = (
        "            event['attempts'] = event.get('attempts', 0) + 1\n"
        if spec["storage_version"] >= 3
        else ""
    )
    retry = (
        "            if event.get('attempts', 0) >= 3: continue\n"
        if spec["storage_version"] >= 3
        else ""
    )
    replay = (
        "    if action == 'replay':\n        for event in state.get('events', []):\n            if not event['sent']: event['attempts'] = 0\n        return {'ok': True}\n"
        if spec["event_replay"]
        else ""
    )
    fallback = (
        f"        provider = registry.get('provider', {spec['ids']['providers'][5]!r})\n"
        if spec["fallback"]
        else "        provider = registry['provider']\n"
    )
    return f"""import copy
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).parent
CLIENTS = {spec["ids"]["clients"]!r}

def load(path):
    return json.loads((ROOT / path).read_text())

def acknowledged(value):
    {acknowledge}

def execute(client, request, state, token, transport=None):
    if client not in CLIENTS: raise ValueError('unknown client')
    request, token = copy.deepcopy(request), copy.deepcopy(token)
    tenant, action = request.get('tenant'), request.get('action')
    if not isinstance(tenant, str) or not tenant: raise ValueError('tenant')
    auth = load('config/auth.json')
    if token.get('role') not in auth['roles']: raise ValueError('role')
{auth}    if transport is None: transport = lambda *args: True
    receipts = {receipt_read}
    if action == 'send':
        text, identity = request.get('text'), request.get('id')
        if not isinstance(text, str) or not text.strip() or not isinstance(identity, str) or not identity: raise ValueError('message')
        if {limit} > load('contracts/message.json')['limit']: raise ValueError('budget')
        key = tenant + ':' + identity
        if key in receipts:
            old = receipts[key]
{conflict}            return copy.deepcopy(old['result'])
        registry = load('config/routes/' + client + '.json')
{fallback}        metadata = load('contracts/providers/' + provider + '.json')
        module = importlib.import_module('providers.' + provider)
        if metadata['protocol'] != module.PROTOCOL: raise ValueError('protocol')
        raw = module.deliver(text)
        reply = {normalized}
        runtime = load('config/runtime.json')
        payload = {{'id': identity, 'text': reply, 'tenant': tenant, 'provider': provider, 'timeout_seconds': {timeout}}}
        try:
            accepted = acknowledged(transport('send', copy.deepcopy(payload)))
        except OSError:
            accepted = False
        if not accepted: return {{'ok': False, 'error': 'unavailable'}}
        result = {{'ok': True, 'id': identity, 'text': reply, 'provider': provider}}
{migrations}        state.setdefault({receipt_key!r}, {{}})[key] = {{'input': [text, tenant], 'result': copy.deepcopy(result)}}
        state.setdefault('events', []).append({{'id': identity, 'text': reply, 'tenant': tenant, 'sent': False}})
        return result
{replay}    if action != 'flush': raise ValueError('action')
    sent = 0
    for event in state.get('events', []):
        if not event['sent'] and event['tenant'] == tenant:
{retry}            settings = load('events/schema.json')
            payload = {{'id': event['id'], 'text': event['text'], 'version': settings['version']}}
            if settings['version'] >= 2: payload['tenant'] = tenant
            try:
                accepted = acknowledged(transport('event', copy.deepcopy(payload)))
            except OSError:
                accepted = False
{attempts}            if accepted:
                event['sent'] = True
                sent += 1
    return {{'ok': True, 'sent': sent, 'pending': sum(not e['sent'] and e['tenant'] == tenant for e in state.get('events', []))}}
"""


def policy(spec, *, coupled=True):
    providers, clients = spec["ids"]["providers"], spec["ids"]["clients"]
    shared = [
        "solution.py",
        "config/auth.json",
        "config/runtime.json",
        "contracts/message.json",
        "events/**",
        "storage/**",
        "release.json",
    ]
    targets = [
        {
            "id": "gate",
            "command": "python3 checks.py gate",
            "inputs": ["anaxigraph/ci-policy.json", "checks.py"],
            "kind": "check",
        }
    ]
    for index, name in enumerate(providers):
        provider_inputs = [
            f"providers/{name}.py",
            f"contracts/providers/{name}.json",
            "shared/**",
            "platform.lock",
        ]
        if coupled and index == 0:
            provider_inputs.append(f"providers/{providers[1]}.py")
        consumer_inputs = list(shared)
        assigned = [client for n, client in enumerate(clients) if n % len(providers) == index]
        consumer_inputs += [
            path
            for client in assigned
            for path in (f"integrations/{client}.py", f"config/routes/{client}.json")
        ]
        consumed = {spec["routes"][client] for client in assigned}
        # The old provider remains a prerequisite while retained receipts refer to it.
        consumed.add(name)
        targets.extend(
            [
                {
                    "id": "p:" + name,
                    "command": f"python3 checks.py p:{name}",
                    "inputs": provider_inputs,
                    "cache": True,
                    "estimated_seconds": 2,
                },
                {
                    "id": "c:" + name,
                    "command": f"python3 checks.py c:{name}",
                    "inputs": consumer_inputs,
                    "consumes": ["p:" + value for value in sorted(consumed)],
                    "cache": True,
                    "estimated_seconds": 5,
                },
            ]
        )
    return {
        "version": 1,
        "targets": targets,
        "always": ["gate"],
        "fallback": [row["id"] for row in targets],
        "shared_inputs": ["platform.lock"],
        "ignored_inputs": ["BRIEF.md", "contracts.md", "submission.json"],
    }


def files(spec, *, coupled=True):
    ids, stage = spec["ids"], spec["stage"]
    values = {
        "solution.py": engine(spec),
        "config/auth.json": json.dumps({"roles": spec["roles"], "audience": spec["auth_audience"]}),
        "config/runtime.json": json.dumps(
            {
                "timeout": 2 if spec["seconds"] else 2000,
                "unit": "seconds" if spec["seconds"] else "milliseconds",
            }
        ),
        "contracts/message.json": json.dumps(
            {"limit": 64, "unit": "bytes" if spec["bytes"] else "characters"}
        ),
        "events/schema.json": json.dumps({"version": spec["event_version"]}),
        "storage/schema.json": json.dumps({"version": spec["storage_version"]}),
        "platform.lock": json.dumps(
            {
                "codec": spec["platform_version"],
                "python": "3.12",
                "normalization": "NFC" if spec["nfc"] else "none",
            }
        ),
        "release.json": json.dumps({"version": 2 if spec["rollout"] else 1}),
        "providers/__init__.py": "",
        "integrations/__init__.py": "",
        "shared/__init__.py": "",
        "shared/codec.py": "import unicodedata\n\ndef normalize(text):\n    return "
        + ("unicodedata.normalize('NFC', text)" if spec["nfc"] else "text")
        + ".strip().upper()\n",
        "anaxigraph/ci-policy.json": json.dumps(policy(spec, coupled=coupled), indent=2),
        "BRIEF.md": brief(ids),
        "contracts.md": brief(ids),
        "checks.py": CHECK_ADAPTER,
    }
    for index, name in enumerate(ids["providers"]):
        if name not in spec["active"]:
            continue
        source = "from shared.codec import normalize\n\ndef _normalize(text):\n    return normalize(text)\n"
        helper = "_normalize(text)"
        if coupled and index == 0:
            source = f"from providers.{ids['providers'][1]} import _normalize\n"
        if (
            (stage >= 0 and index == 0)
            or (stage >= 9 and index == 1)
            or (stage >= 20 and index == 4)
        ):
            helper = "''.join(_normalize(text))"
        result = (
            f"{{'text': {helper}, 'receipt': {name!r}}}" if name in spec["envelopes"] else helper
        )
        protocol = spec["capability_version"] if index == 0 else 1
        values[f"contracts/providers/{name}.json"] = json.dumps({"protocol": protocol})
        values[f"providers/{name}.py"] = (
            source + f"\nPROTOCOL = {protocol}\n\ndef deliver(text):\n    return {result}\n"
        )
    for client in ids["clients"]:
        values[f"config/routes/{client}.json"] = json.dumps({"provider": spec["routes"][client]})
        values[f"integrations/{client}.py"] = (
            f"from solution import execute\n\ndef send(request, state, token, transport=None):\n    return execute({client!r}, request, state, token, transport)\n"
        )
    return values


def write(root: Path, values):
    root.mkdir(parents=True, exist_ok=True)
    for relative, body in values.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def advance(root, previous, current, *, coupled=True, previous_coupled=None):
    """Only reference qualification uses this; live agents receive the issue instead."""
    before = files(previous, coupled=coupled if previous_coupled is None else previous_coupled)
    after = files(current, coupled=coupled)
    changed = sorted(
        key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
    )
    for key in before.keys() - after.keys():
        (root / key).unlink()
    write(root, {key: after[key] for key in changed if key in after})
    return changed
