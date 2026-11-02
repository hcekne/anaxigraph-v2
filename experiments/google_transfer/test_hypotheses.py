"""Executable hypotheses suggested by a UI prototype, not measured coding-arm results.

All repositories and model answers here are synthetic. These tests exercise real
indexing, persistence, reuse and CI planning without executing candidate code or
calling a model. They do not implement or grade the prototype's fictional products.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from anaxigraph.application import Application
from anaxigraph.workflows.engine import WorkflowRunner


def write(root, path, source):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)


def synthetic_answer(request, *_args, **_kwargs):
    packet = request["packet"]
    if request["kind"] == "architecture":
        return {
            "producer": "synthetic-transfer-hypothesis",
            "lens": packet["lens"],
            "assessments": [
                {"module_id": identity, "outcome": "keep"} for identity in request["scope"]
            ],
            "findings": [],
            "uncertainty": ["Synthetic answers establish no architecture quality."],
        }
    assert request["kind"] == "annotate"
    return {
        "producer": "synthetic-transfer-hypothesis",
        "annotations": [
            {
                "node_id": module["id"],
                "overview": "Provides an observable result to callers.",
                "detail": "Preserves the declared operation and its input obligations.",
                "capabilities": ["Provide an observable result."],
                "functions": [
                    {"node_id": fn["id"], "overview": "Return an observable result."}
                    for fn in packet["functions"]
                    if fn["path"] == module["path"]
                ],
                "contracts": [],
                "uncertainty": ["Behavior has not been independently executed."],
            }
            for module in packet["modules"]
        ],
    }


def install_policy(root, inputs):
    write(
        root,
        "anaxigraph/ci-policy.json",
        json.dumps(
            {
                "version": 1,
                "targets": [
                    {"id": "local", "command": "pytest tests/test_local.py", "inputs": inputs},
                    {"id": "full", "command": "pytest", "inputs": ["**"], "cadence": "scheduled"},
                ],
                "fallback": ["full"],
            }
        ),
    )


def supply_omitted_consumer(application, path):
    """Caller-supplied observations, explicitly not tests executed by this suite."""
    value = {
        "version": 1,
        "inputs": dict(application._current().snapshot.files),
        "environment": {"python": "synthetic-transfer-runtime"},
        "tests": ["tests/test_local.py::test_unit", "tests/test_consumer.py::test_contract"],
        "files": [{"path": path, "executing": [0, 1], "selected": [0]}],
        "faults": [],
    }
    write(application.root, "anaxigraph/ci-evidence.json", json.dumps(value))
    application.scan()
    return value["environment"]


@pytest.mark.parametrize(
    "dispatch",
    [
        "import provider\ndef dispatch(name):\n    return getattr(provider, name)()\n",
        "from importlib import import_module\n"
        "def dispatch(name):\n    return import_module(name).run()\n",
        "registry = {}\ndef register(name):\n"
        "    def decorate(fn):\n        registry[name] = fn\n        return fn\n"
        "    return decorate\n@register('primary')\ndef run():\n    return 1\n"
        "def dispatch(name):\n    return registry[name]()\n",
    ],
    ids=["getattr", "importlib-alias", "decorator-registry"],
)
def test_dynamic_dispatch_keeps_uncertainty_and_observed_consumer_gap(tmp_path, dispatch):
    marker = tmp_path / "candidate-executed"
    write(tmp_path, "provider.py", "def run():\n    return 1\n")
    write(tmp_path, "dispatcher.py", dispatch + f"open({str(marker)!r}, 'w').write('bad')\n")
    install_policy(tmp_path, ["dispatcher.py"])
    application = Application(tmp_path)
    application.scan()
    graph = application._current()
    dispatch_calls = [
        edge for edge in graph.edges if edge.source == "symbol:dispatcher.py#dispatch"
    ]
    assert dispatch_calls
    assert any(edge.resolution != "resolved" for edge in dispatch_calls)
    assert not any(
        edge.kind == "calls"
        and edge.resolution == "resolved"
        and edge.target in {"symbol:provider.py#run", "symbol:dispatcher.py#run"}
        for edge in dispatch_calls
    )
    declared = application.ci_plan(changed_paths=["dispatcher.py"])
    assert declared["selection_evidence"]["status"] == "not_supplied"
    assert declared["checks_executed"] is False
    assert "not proof of behavioral compatibility" in declared["assurance"]
    runtime = supply_omitted_consumer(application, "dispatcher.py")
    observed = application.ci_plan(changed_paths=["dispatcher.py"], runtime_context=runtime)
    assert observed["status"] == "selection_review_required"
    assert observed["execution_ready"] is False
    assert observed["selection_evidence"]["findings"][0]["omitted_tests"] == [
        "tests/test_consumer.py::test_contract"
    ]
    assert not marker.exists()


CONTRACT = (
    "from dataclasses import dataclass\n@dataclass\nclass Record:\n    amount: int = 1\n"
    "def quote(record: Record) -> int:\n    return record.amount\n"
)


@pytest.mark.parametrize(
    ("edit", "caller_status", "implementation_changed"),
    [
        ("# Existing behavior explained.\n" + CONTRACT, "current", False),
        (CONTRACT.replace("return record.amount", "return record.amount + 1"), "current", True),
        (CONTRACT.replace("amount: int = 1", "amount: int = 2"), "stale", True),
        (
            CONTRACT.replace("amount: int = 1", "amount: int = 1\n    currency: str = 'USD'"),
            "stale",
            True,
        ),
    ],
    ids=["comment", "same-signature-behavior", "dataclass-default", "dataclass-schema"],
)
def test_shared_contract_edits_distinguish_current_understanding_from_behavior(
    tmp_path, edit, caller_status, implementation_changed
):
    write(tmp_path, "contract.py", CONTRACT)
    write(
        tmp_path,
        "consumer.py",
        "from contract import Record, quote\ndef total():\n    return quote(Record())\n",
    )
    application = Application(tmp_path)
    mapped = WorkflowRunner(application, synthetic_answer).start("mapping", background=False)
    assert mapped["status"] == "completed", mapped
    baseline = application.prepare_change("Change quote amount")
    write(tmp_path, "contract.py", edit)
    application.scan()
    data = application.inspect("consumer.py", full=True)["entity"]["data"]
    assert data["annotation_status"] == caller_status
    assert data["dependency_implementation_changed"] is implementation_changed
    assert data["behavioral_validation"] == "not_independently_verified"
    assert application.inspect("contract.py")["entity"]["data"]["annotation_status"] == "stale"
    validation = application.validate_change(baseline_id=baseline["baseline_id"])
    assert validation["changed_paths"] == ["contract.py"]
    assert validation["behavioral_status"] == "unchecked"


@pytest.mark.parametrize("reverse_creation", [False, True], ids=["forward", "reverse"])
def test_owner_retrieval_is_order_invariant_and_repeated_noise_adds_no_score(
    tmp_path, reverse_creation
):
    sources = {
        "timeline.py": '"""retroactive"""\ndef store(value):\n    return value\n',
        "compensation.py": "def apply(value):\n    return value\n",
    }
    for path in reversed(sources) if reverse_creation else sources:
        write(tmp_path, path, sources[path])
    application = Application(tmp_path)
    application.scan()
    request = application.analysis_request("annotate", ["compensation.py"])
    result = synthetic_answer(request)
    result["annotations"][0]["capabilities"] = ["Apply retroactive fee royalty clawback."]
    application.submit_analysis(request["request_id"], result)
    before = application.prepare_change("retroactive fee royalty clawback")["likely_owners"]
    by_path = {row["path"]: row for row in before}
    assert any(
        reason.startswith("current_annotation:")
        for reason in by_path["compensation.py"]["match_reasons"]
    )
    assert before[0]["path"] == "compensation.py"
    write(
        tmp_path, "timeline.py", sources["timeline.py"].replace("retroactive", "retroactive " * 100)
    )
    application.scan()
    after = application.prepare_change("retroactive fee royalty clawback")["likely_owners"]
    assert [(row["path"], row["score"]) for row in after] == [
        (row["path"], row["score"]) for row in before
    ]
    # This controlled vocabulary checks retrieval mechanics, not coding-arm superiority.


@pytest.mark.parametrize("kind", ["mapping", "architecture"])
def test_unchanged_refresh_reuses_persisted_understanding_without_portable_recovery(tmp_path, kind):
    write(tmp_path, "unit.py", "def read(value):\n    return value\n")
    calls = Counter()

    def provider(request, *_args, **_kwargs):
        calls[request["kind"]] += 1
        return synthetic_answer(request)

    first = WorkflowRunner(Application(tmp_path), provider).start(kind, background=False)
    assert first["status"] == "completed", first
    assert calls["annotate"] == 1
    (tmp_path / "anaxigraph/record.json").unlink()

    def no_calls(*_args, **_kwargs):
        pytest.fail("An unchanged refresh unexpectedly called the model provider")

    restarted = Application(tmp_path)
    repeated = WorkflowRunner(restarted, no_calls).start(kind, background=False)
    assert repeated["status"] == "completed", repeated
    assert repeated["semantic_refresh"]["reused_modules"] == 1
    assert sum(row["model_calls"] for row in repeated["stage_timings"]) == 0
    assert restarted.status()["annotations"] == {"current": 1, "stale": 0, "missing": 0}
    if kind == "architecture":
        reviews = restarted.reviews("architecture")["items"]
        assert len(reviews) == 3 and all(row["status"] == "current" for row in reviews)


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("rules.loom", "phase commit { require conservation; }\n"),
        ("migration.sql", "ALTER TABLE records ADD COLUMN tenant TEXT;\n"),
        ("web/client.ts", "export interface Record { tenant: string }\n"),
        ("schema.json", '{"type":"object","required":["tenant"]}\n'),
    ],
    ids=["loom", "sql", "typescript", "schema"],
)
def test_unparsed_inputs_are_fingerprinted_and_unowned_changes_retain_fallback(
    tmp_path, path, source
):
    write(tmp_path, "compiler.py", "def compile_policy(value):\n    return value\n")
    write(tmp_path, path, source)
    install_policy(tmp_path, ["compiler.py"])
    application = Application(tmp_path)
    application.scan()
    before = application._current()
    assert path in before.snapshot.files
    assert not any(node.path == path for node in before.nodes)
    application.prepare_change("Change external policy contract")
    write(tmp_path, path, source + "\n")
    application.scan()
    assert application._current().snapshot.files[path] != before.snapshot.files[path]
    plan = application.ci_plan()
    assert plan["changed_paths"] == [path]
    assert plan["uncovered_paths"] == [path]
    assert [row["id"] for row in plan["selected_targets"]] == ["full"]
    assert plan["status"] == "incomplete" and plan["execution_ready"] is False
    assert plan["checks_executed"] is False
    assert plan["savings_seconds"] is None
