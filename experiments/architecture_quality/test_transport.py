"""Fake processes exercise the complete live-shaped path; every artifact is synthetic."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import run

RUNTIME = r"""
import json, sqlite3, sys
from pathlib import Path
from anaxigraph.contracts import Graph, Snapshot, Node
from anaxigraph.storage import Store
args = sys.argv[1:]
root = Path(args[args.index('--repo') + 1])
args = args[2:]
directory = root / '.anaxigraph-v2'
directory.mkdir(exist_ok=True)
def record(kind, payload):
    with sqlite3.connect(directory / 'trial.sqlite') as db:
        db.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,kind TEXT,payload TEXT)')
        db.execute('INSERT INTO events(kind,payload) VALUES(?,?)', (kind,json.dumps(payload)))
def review(kind):
    result = {'alternatives': [{'name': str(i)} for i in range(3)]} if kind == 'fresh-eyes' else {'findings': ['remove private cross-owner dependency']}
    return {'entity': {'data': {'result': {'kind': kind, 'result': result}}}}
operation = args[0]
if operation in ('scan', 'status'):
    result = {'snapshot': {'id': 'synthetic-snapshot'}, 'modules': 7, 'annotations': {'current': 7}}
elif operation == 'prepare':
    record('task_started', {})
    record('context_supplied', {})
    result = {'baseline_id': 'synthetic-baseline'}
elif operation == 'run':
    kind = args[1]
    record('model_call', {'provider': 'synthetic', 'model': 'gpt-6.1-sol', 'reasoning_effort': 'xhigh', 'input_tokens': 11, 'output_tokens': 5, 'cached_input_tokens': 0, 'elapsed_seconds': .01, 'cost_usd': None})
    record('model_call', {'provider': 'synthetic', 'model': 'gpt-6.1-sol', 'status': 'reused', 'reused': True, 'elapsed_seconds': 0, 'input_tokens': None, 'output_tokens': None, 'cost_usd': None})
    store = Store(directory / 'knowledge.sqlite')
    store.save_graph(Graph(Snapshot('synthetic-snapshot', str(root), 'base', True, {'compute.py': 'one'}, '2026-10-28T00:00:00Z'), (Node('module:compute.py','module','compute','compute.py'),), (), ()))
    if kind == 'fresh-eyes':
        packet = {'kind': 'fresh-eyes', 'packet': {'instruction': 'behavior only', 'requirements': [{'id': 'R1', 'behavior': 'compute a price and submit once'}], 'charter': [{'principle': 'small explicit behavioral contracts'}], 'blindness': 'no source'}}
        store.save_review(str(root), 'synthetic-snapshot', 'blind-request', 'request', packet)
    (root / 'anaxigraph').mkdir(exist_ok=True)
    (root / 'anaxigraph/record.json').write_text('{"portable": "synthetic review export"}')
    result = {'status': 'completed', 'snapshot_id': 'synthetic-snapshot', 'reconciliation': {'status': 'completed', 'declared_modules': 4 if kind == 'patterns' else 7, 'excluded_modules': [{}, {}, {}] if kind == 'patterns' else [], 'product_review_id': 'fresh-eyes-id', 'expected_pairs': 7, 'evaluated_pairs': 7}}
elif operation == 'reviews':
    result = {'items': [{'id': kind + '-id', 'kind': kind, 'status': 'current'} for kind in ('patterns', 'fresh-eyes')], 'next_offset': None}
elif operation == 'inspect':
    result = {'entity': {'data': {'principles': []}}} if args[1] == 'charter' else review(args[1].removeprefix('review:').removesuffix('-id'))
else:
    raise ValueError(operation)
print(json.dumps(result))
"""

AGENT = r"""
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, SOURCE_REPOSITORY)
from experiments.architecture_quality.run import synthetic_step
arguments = sys.argv[1:]
root = Path(arguments[arguments.index('--cd') + 1])
runtime_arg = next((value for value in arguments if value.startswith('mcp_servers.anaxigraph.command=')), None)
runtime = json.loads(runtime_arg.split('=', 1)[1]) if runtime_arg else None
prompt = sys.stdin.read()
def emit(value):
    print(json.dumps(value))
def call(tool, args):
    cli = {'scan': ['scan'], 'prepare_change': ['prepare', 'qualification'], 'inspect': ['inspect', args.get('entity_id', 'charter')]}[tool]
    reply = json.loads(subprocess.check_output([runtime, '--repo', str(root), *cli], text=True))
    emit({'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'status': 'completed', 'server': 'anaxigraph', 'tool': tool, 'arguments': args, 'result': {'structured_content': reply, 'is_error': False}}})
emit({'type': 'thread.started', 'thread_id': 'synthetic-' + root.name})
if 'qualification' in prompt and 'No shell, edits' in prompt:
    call('scan', {})
    call('prepare_change', {'task': 'Architecture outcome qualification'})
    call('inspect', {'entity_id': 'charter'})
elif prompt.startswith('Review this small application'):
    emit({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Synthetic generic source review: remove the private adapter dependency.'}})
else:
    if runtime:
        call('inspect', {'entity_id': 'review:patterns-id'})
        call('inspect', {'entity_id': 'review:fresh-eyes-id'})
    synthetic_step(root, 'checkout', 0, runtime is not None)
    if runtime:
        (root / 'decisions.json').write_text(json.dumps([{'advice_id': 'patterns-id', 'disposition': 'adopt', 'reason': 'remove source-observed private dependency', 'files': ['compute.py']}]))
emit({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 4, 'cached_input_tokens': 0}})
"""


def test_fake_subprocess_preflight_freeze_reviews_coding_checks_and_provenance(tmp_path):
    runtime, agent = tmp_path / "runtime", tmp_path / "codex-fixture"
    runtime.write_text(f"#!{sys.executable}\n" + RUNTIME)
    agent.write_text(
        f"#!{sys.executable}\n"
        + AGENT.replace("SOURCE_REPOSITORY", repr(str(Path(__file__).resolve().parents[2])))
    )
    runtime.chmod(0o755)
    agent.chmod(0o755)
    state = tmp_path / "state"
    config = run.parser().parse_args(
        [
            "--live",
            "--state",
            str(state),
            "--runtime",
            str(runtime),
            "--agent",
            str(agent),
            "--scenarios",
            "checkout",
            "--stages",
            "1",
            "--control-mode",
            "review",
        ]
    )
    # A custom client is synthetic by construction, even with a live-shaped invocation.
    result = run.run_trial(config)
    assert result["status"] == "completed" and result["evidence_kind"] == "synthetic"
    proof = json.loads((state / "preflight.json").read_text())
    assert proof["status"] == "passed" and proof["evidence_kind"] == "synthetic"
    advice = json.loads((state / "checkout-0" / "advice.private.json").read_text())
    assert advice["baseline_digest"] == result["pairs"][0]["starting_digest"]
    assert len(advice["blind_packets"]) == 1
    assert len(advice["model_receipts"]) == 3
    assert len(advice["cache_receipts"]) == 3
    treatment = result["pairs"][0]["arms"]["treatment"][0]
    assert treatment["advice_consulted"]
    assert treatment["dispositions"][0]["changed_source_evidence_verified"]
    assert treatment["outcome"]["qualified"]
    comparison = json.loads((state / "comparison.json").read_text())
    assert comparison["evidence_kind"] == "synthetic"
    for arm in ("control", "treatment"):
        assert comparison["pairs"][0]["arms"][arm]["total_model_call_opportunities"] == 4
        assert comparison["pairs"][0]["arms"][arm]["delivery_complete"] is None


def test_synthetic_preflight_cannot_be_reused_to_qualify_a_live_run(tmp_path, monkeypatch):
    config = run.parser().parse_args(["--live", "--state", str(tmp_path), "--runtime", "/bin/true"])
    monkeypatch.setattr(run, "client_identity", lambda config: {"available": True})
    monkeypatch.setattr(
        run, "preflight", lambda config: {"status": "passed", "evidence_kind": "synthetic"}
    )
    assert run.run_trial(config)["status"] == "preflight_failed"
