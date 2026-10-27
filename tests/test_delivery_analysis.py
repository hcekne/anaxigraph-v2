"""Root CI advice covers declarations even without parsed or annotated modules."""

import copy
import json

import pytest

from anaxigraph.analysis.delivery import STRATEGIES, capture_coverage, reconcile_delivery
from anaxigraph.analysis.packets import build_request, plan_scopes
from anaxigraph.analysis.portable import input_identity
from anaxigraph.analysis.validation import canonical_result, validate_result
from anaxigraph.indexing import index
from anaxigraph.indexing.ci_evidence import capture_ci_evidence


@pytest.fixture
def setup(tmp_path):
    target = tmp_path / ".github/workflows/ci.yml"
    target.parent.mkdir(parents=True)
    target.write_text("on: pull_request\njobs:\n  test:\n    steps:\n      - run: npm test\n")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest --run"}}))
    (tmp_path / "index.js").write_text("console.log('not executed')")
    graph = index(tmp_path)
    context = capture_ci_evidence(tmp_path, graph.snapshot.files)
    return graph, context


def request(graph, context):
    return build_request(graph, "delivery", delivery_context=context)


def answer(packet, outcome="finding"):
    identities = packet["packet"]["pipeline_ids"]
    recommendation = {
        "pipeline_ids": identities,
        "priority": 2,
        "title": "Declare independent component verification",
        "bottleneck": "The declared pipeline invokes one repository-wide test command.",
        **{
            field: [
                "Proposed: inspect declared component inputs; retain required checks until coverage is established."
            ]
            for field in STRATEGIES
        },
        "evidence": [{"path": identity, "line": 5} for identity in identities],
        "uncertainty": ["No attributed job duration measurements are available."],
    }
    return {
        "producer": "synthetic contract fixture",
        "assessments": [{"pipeline_id": identity, "outcome": outcome} for identity in identities],
        "recommendations": [recommendation] if outcome == "finding" else [],
        "uncertainty": ["CI coverage is not measured."] if outcome == "unknown" else [],
    }


def test_js_only_unannotated_repo_gets_one_root_request_with_real_ci_evidence(setup):
    graph, context = setup
    assert not graph.nodes
    packet = request(graph, context)
    rebuilt = build_request(graph, "delivery", [], pipeline_paths=None, delivery_context=context)
    assert rebuilt["request_id"] == packet["request_id"]
    assert rebuilt["request_options"]["pipeline_paths"] is None
    assert plan_scopes(graph, "delivery") == [[]]
    assert packet["scope"] == [] and packet["function_ids"] == {}
    assert packet["packet"]["pipeline_ids"] == [".github/workflows/ci.yml"]
    assert (
        packet["packet"]["delivery_context"]["package_scripts"]["scripts"][0]["command"]
        == "vitest --run"
    )
    assert "do not authorize skipping CI" in packet["packet"]["instruction"]
    assert "required status" in packet["packet"]["instruction"]
    validate_result(packet, answer(packet), graph)
    for outcome in ("keep", "unknown"):
        validate_result(packet, answer(packet, outcome), graph)


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda result: result["assessments"].append(result["assessments"][0]), "exactly once"),
        (lambda result: result["assessments"][0].update(outcome="keep"), "match finding"),
        (lambda result: result["assessments"][0].update(pipeline_id="other"), "unexpected"),
        (
            lambda result: result["recommendations"][0].update(pipeline_ids=["other"]),
            "requested pipeline",
        ),
        (lambda result: result["recommendations"][0].update(priority=True), "priority"),
        (lambda result: result["recommendations"][0].update(fast_path=[]), "fast_path"),
        (
            lambda result: result["recommendations"][0].update(broaden_when=["x" * 301]),
            "broaden_when",
        ),
        (lambda result: result["recommendations"][0].update(evidence=[]), "source evidence"),
        (
            lambda result: result["recommendations"][0]["evidence"][0].update(line=6),
            "source bounds",
        ),
        (
            lambda result: result["recommendations"][0]["evidence"][0].update(line=True),
            "integer line",
        ),
        (
            lambda result: result["recommendations"][0]["evidence"][0].update(path="../ci.yml"),
            "mapped path",
        ),
        (
            lambda result: result["recommendations"][0].update(
                evidence=[{"path": "package.json", "line": 1}]
            ),
            "every named pipeline",
        ),
    ],
)
def test_scope_strategy_and_exact_ci_line_evidence_are_checked(setup, mutation, message):
    graph, context = setup
    packet = request(graph, context)
    result = answer(packet)
    mutation(result)
    with pytest.raises(ValueError, match=message):
        validate_result(packet, result, graph)


def test_unknown_requires_explicit_missing_evidence(setup):
    graph, context = setup
    packet = request(graph, context)
    result = answer(packet, "unknown")
    result["uncertainty"] = []
    with pytest.raises(ValueError, match="uncertainty"):
        validate_result(packet, result, graph)


def test_no_ci_configuration_uses_project_sentinel_and_real_manifest_evidence(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest"}}')
    graph = index(tmp_path)
    packet = request(graph, capture_ci_evidence(tmp_path, graph.snapshot.files))
    result = answer(packet)
    result["recommendations"][0]["evidence"] = [{"path": "package.json", "line": 1}]
    assert packet["packet"]["pipeline_ids"] == ["project"]
    validate_result(packet, result, graph)


def test_canonical_projection_preserves_bad_required_fields_for_validation(setup):
    graph, context = setup
    packet = request(graph, context)
    result = answer(packet)
    result["raw_source"] = "never store"
    result["assessments"][0]["raw_cache"] = "never store"
    result["recommendations"][0]["raw_source"] = "never store"
    result["recommendations"][0]["evidence"][0]["raw_source"] = "never store"
    canonical = canonical_result(packet, result)
    assert "never store" not in json.dumps(canonical)
    validate_result(packet, canonical, graph)
    result["recommendations"][0]["fast_path"] = None
    with pytest.raises(ValueError, match="fast_path"):
        validate_result(packet, canonical_result(packet, result), graph)


def test_filtered_paths_are_root_options_and_other_kinds_keep_existing_option_contract(setup):
    graph, context = setup
    paths = [".github/workflows/ci.yml"]
    packet = build_request(graph, "delivery", paths, delivery_context=context)
    assert packet["scope"] == []
    assert packet["request_options"]["pipeline_paths"] == paths
    with pytest.raises(ValueError, match="same CI paths"):
        build_request(graph, "delivery", ["else"], pipeline_paths=paths, delivery_context=context)
    with pytest.raises(ValueError, match="apply only"):
        build_request(graph, "annotate", delivery_context=context)
    with pytest.raises(ValueError, match="captured pipeline"):
        build_request(graph, "delivery")
    with pytest.raises(ValueError, match="distinct CI"):
        build_request(graph, "delivery", pipeline_paths=paths * 2, delivery_context=context)


def test_portable_identity_tracks_ci_component_contract_and_policy_without_leaf_body_churn(
    setup, tmp_path
):
    graph, context = setup
    before = request(graph, context)
    (tmp_path / "internal.py").write_text("def helper():\n    return 'changed private body'\n")
    after_graph = index(tmp_path)
    after = request(after_graph, capture_ci_evidence(tmp_path, after_graph.snapshot.files))
    assert before["request_id"] != after["request_id"]
    assert input_identity(before) == input_identity(after)
    for change in (
        {"components": [{"public_surface": "new contract"}]},
        {"policy": "new required check"},
    ):
        assert input_identity(before) != input_identity(request(graph, context | change))
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(ci.read_text().replace("npm test", "npm run test:payments"))
    ci_graph = index(tmp_path)
    assert input_identity(before) != input_identity(
        request(ci_graph, capture_ci_evidence(tmp_path, ci_graph.snapshot.files))
    )


def test_reconciliation_requires_single_accepted_root_review(setup):
    graph, context = setup
    packet = request(graph, context)
    result = answer(packet)
    row = {
        "kind": "delivery",
        "result": {"scope": [], "result": result, "delivery_coverage": capture_coverage(context)},
    }
    summary = reconcile_delivery([row])
    assert summary["assessed_pipelines"] == 1
    assert summary["outcomes"] == {"finding": 1}
    assert summary["recommendations"] == 1
    assert summary["scope_status"] == "reviewed"
    partial = copy.deepcopy(row)
    partial["result"]["delivery_coverage"]["partial_evidence"] = True
    assert reconcile_delivery([partial])["scope_status"] == "partial_evidence"
    legacy = copy.deepcopy(row)
    del legacy["result"]["delivery_coverage"]
    assert reconcile_delivery([legacy])["scope_status"] == "partial_evidence"
    for rows in ([], [row, row], [row | {"kind": "architecture"}]):
        with pytest.raises(ValueError, match="exactly one"):
            reconcile_delivery(rows)
    altered = copy.deepcopy(row)
    altered["result"]["scope"] = ["module:internal.py"]
    with pytest.raises(ValueError, match="root scope"):
        reconcile_delivery([altered])


@pytest.mark.parametrize(
    "context_field, flag",
    [
        ("ci_policy", "targets_truncated"),
        ("components", "areas_truncated"),
        ("components", "dependencies_truncated"),
    ],
)
def test_capture_coverage_keeps_policy_and_component_projection_limits_visible(
    setup, context_field, flag
):
    _, context = setup
    assert capture_coverage(context)["partial_evidence"] is False
    assert capture_coverage(context | {context_field: {flag: True}})["partial_evidence"] is True
