"""Focused searches declare coverage without manufacturing findings or CI results."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from anaxigraph.analysis.architecture import LENSES, architecture_scopes, reconcile_architecture
from anaxigraph.analysis.packets import build_request, plan_scopes
from anaxigraph.analysis.portable import input_identity
from anaxigraph.analysis.validation import canonical_result, validate_result
from anaxigraph.indexing import index


@pytest.fixture
def graph(tmp_path):
    sources = {
        "pkg/__init__.py": "pass\n",
        "pkg/a.py": "def produce(value):\n    return value\n",
        "pkg/b.py": "from .a import produce\ndef deliver(value):\n    return produce(value)\n",
        "other.py": "def separate(value):\n    return value\n",
        "tests/test_a.py": "from pkg.a import produce\ndef test_result():\n    assert produce(1) == 1\n",
    }
    for path, source in sources.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    original = index(tmp_path)
    return replace(
        original,
        nodes=tuple(
            replace(
                node,
                data=node.data
                | {
                    "annotation_status": "current",
                    "annotation": {
                        "overview": "Transforms a supplied value.",
                        "detail": "Produces an output for callers.",
                        "capabilities": ["Transforms a supplied value."],
                        "contracts": [],
                        "uncertainty": [],
                    },
                },
            )
            if node.kind == "module"
            else node
            for node in original.nodes
        ),
    )


def request(graph, lens="boundaries"):
    return build_request(graph, "architecture", ["pkg/a.py", "pkg/b.py"], lens=lens)


def answer(packet, *, finding=True):
    result = {
        "producer": "synthetic contract fixture",
        "lens": packet["request_options"]["lens"],
        "assessments": [
            {"module_id": identity, "outcome": "finding" if finding else "keep"}
            for identity in packet["scope"]
        ],
        "findings": [],
        "uncertainty": [],
    }
    if finding:
        result["findings"] = [
            {
                "title": "Isolate reusable transformation behavior",
                "module_ids": packet["scope"],
                "priority": 2,
                "placement": "local-package",
                "public_contract": "Transform a supplied value while preserving caller-visible errors.",
                "private_implementation": "Hide internal transformation helpers.",
                "dependencies": [],
                "verification": ["Proposed: run behavior checks through the public boundary."],
                "next_step": "Inspect consumers before selecting a package boundary.",
                "options": [
                    {
                        "placement": placement,
                        "benefit": "Preserve or isolate responsibility.",
                        "cost": "Account for ownership and release coordination.",
                    }
                    for placement in (
                        "in-place",
                        "local-package",
                        "workspace-distribution",
                        "separate-repository",
                    )
                ],
                "evidence": [{"path": "pkg/a.py", "line": 1}, {"path": "pkg/b.py", "line": 1}],
                "uncertainty": ["Independent release demand has not been demonstrated."],
            }
        ]
    return result


def row(packet, result):
    return {
        "id": packet["request_id"],
        "kind": "architecture",
        "result": {
            "scope": packet["scope"],
            "request_options": packet["request_options"],
            "result": result,
        },
    }


def test_scopes_are_cross_file_neighborhoods_and_explicit_test_scope_is_honored(graph):
    assert architecture_scopes(graph) == [
        ["module:other.py"],
        ["module:pkg/a.py", "module:pkg/b.py"],
    ]
    assert plan_scopes(graph, "architecture") == architecture_scopes(graph)
    assert architecture_scopes(graph, ["tests/test_a.py"]) == [["module:tests/test_a.py"]]
    assert plan_scopes(graph, "architecture", batch_size=1) == [
        ["module:other.py"],
        ["module:pkg/a.py"],
        ["module:pkg/b.py"],
    ]
    with pytest.raises(ValueError, match="scope"):
        architecture_scopes(graph, ["missing.py"])
    with pytest.raises(ValueError, match="batch_size"):
        architecture_scopes(graph, batch_size=0)


def test_scopes_split_large_directories_without_mixing_responsibility_groups(graph):
    node = next(node for node in graph.nodes if node.path == "pkg/a.py" and node.kind == "module")
    nodes = tuple(
        replace(node, id=f"module:pkg/n{i:02}.py", path=f"pkg/n{i:02}.py") for i in range(18)
    )
    groups = architecture_scopes(replace(graph, nodes=nodes))
    assert [len(group) for group in groups] == [8, 8, 2]
    assert len({identity for group in groups for identity in group}) == 18


def test_packet_lenses_have_distinct_input_identity_and_honest_context(graph):
    packets = [request(graph, lens) for lens in LENSES]
    assert len({packet["request_id"] for packet in packets}) == 3
    assert len({input_identity(packet) for packet in packets}) == 3
    for packet in packets:
        assert packet["packet"]["lens"] == packet["request_options"]["lens"]
        assert "No raw test/CI source" in packet["packet"]["instruction"]
        assert "source" not in packet["packet"]
        assert any(item["is_test"] for item in packet["packet"]["neighbor_context"]["modules"])
    default = build_request(graph, "architecture", ["pkg/a.py"])
    assert default["request_options"]["lens"] == "boundaries"


def test_current_neighbor_behavior_is_bounded_and_stale_interpretations_are_not_presented(graph):
    initial = request(graph)
    test_neighbor = next(
        item for item in initial["packet"]["neighbor_context"]["modules"] if item["is_test"]
    )
    assert test_neighbor["overview"] == "Transforms a supplied value."
    changed = replace(
        graph,
        nodes=tuple(
            replace(
                node,
                data=node.data
                | {
                    "annotation": node.data["annotation"]
                    | {
                        "overview": "Different observed behavior. " * 20,
                        "capabilities": ["Capability " * 50] * 4,
                    }
                },
            )
            if node.id == test_neighbor["id"]
            else node
            for node in graph.nodes
        ),
    )
    updated = request(changed)
    assert input_identity(updated) != input_identity(initial)
    neighbor = next(
        item for item in updated["packet"]["neighbor_context"]["modules"] if item["is_test"]
    )
    assert len(neighbor["overview"]) == 180
    assert len(neighbor["capabilities"]) == 2 and all(
        len(value) == 180 for value in neighbor["capabilities"]
    )
    stale = replace(
        changed,
        nodes=tuple(
            replace(node, data=node.data | {"annotation_status": "stale"})
            if node.id == test_neighbor["id"]
            else node
            for node in changed.nodes
        ),
    )
    neighbor = next(
        item for item in request(stale)["packet"]["neighbor_context"]["modules"] if item["is_test"]
    )
    assert neighbor["annotation_status"] == "stale"
    assert "overview" not in neighbor and "capabilities" not in neighbor


@pytest.mark.parametrize("lens", ["", "factory", [], None])
def test_bad_lenses_are_rejected_or_only_none_uses_manual_default(graph, lens):
    if lens is None:
        assert request(graph, lens)["packet"]["lens"] == "boundaries"
    else:
        with pytest.raises(ValueError, match="lens"):
            request(graph, lens)


def test_old_requests_keep_existing_option_shape_and_fresh_eyes_has_no_architecture_context(graph):
    for kind in ("patterns", "pattern-screen", "fresh-eyes"):
        packet = build_request(graph, kind, ["pkg/a.py"])
        assert packet["request_options"] == {
            "max_packet_bytes": 2_000_000,
            "max_result_bytes": 8_000_000,
            "pattern_ids": None,
            "level": "subsystem",
        }
        assert "lens" not in packet["packet"]
        with pytest.raises(ValueError, match="only"):
            build_request(graph, kind, ["pkg/a.py"], lens="packages")
    blind = build_request(graph, "fresh-eyes", ["pkg/a.py"])["packet"]
    assert "neighbor_context" not in blind and "relationships" not in blind


def test_current_understanding_is_required_for_source_aware_scouts(graph):
    with pytest.raises(ValueError, match="pattern_ids"):
        build_request(graph, "architecture", ["pkg/a.py"], pattern_ids=["adapter"])
    stale = replace(
        graph,
        nodes=tuple(
            replace(node, data=node.data | {"annotation_status": "stale"})
            if node.path == "pkg/a.py"
            else node
            for node in graph.nodes
        ),
    )
    with pytest.raises(ValueError, match="current"):
        request(stale)


@pytest.mark.parametrize("lens", LENSES)
def test_evidence_backed_proposals_and_keep_or_unknown_are_all_legal(graph, lens):
    packet = request(graph, lens)
    validate_result(packet, answer(packet), graph)
    keep = answer(packet, finding=False)
    validate_result(packet, keep, graph)
    keep["assessments"][0]["outcome"] = "unknown"
    with pytest.raises(ValueError, match="uncertainty"):
        validate_result(packet, keep, graph)
    keep["uncertainty"] = ["Relevant build and CI targets have not been inspected."]
    validate_result(packet, keep, graph)


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda result: result.update(lens="verification"), "lens"),
        (lambda result: result["assessments"].pop(), "every scoped"),
        (lambda result: result["assessments"].append(result["assessments"][0]), "every scoped"),
        (lambda result: result["assessments"][0].update(outcome="keep"), "members"),
        (lambda result: result["findings"][0]["module_ids"].append("module:other.py"), "scoped"),
        (lambda result: result["findings"][0]["evidence"].pop(), "every member"),
        (lambda result: result["findings"][0]["evidence"][0].update(line=9000), "outside"),
        (lambda result: result["findings"][0].update(priority=True), "priority"),
        (lambda result: result["findings"][0].update(verification=[]), "verification"),
        (lambda result: result["findings"][0].update(public_contract=""), "public_contract"),
        (lambda result: result["findings"][0].update(placement="microservice-magic"), "placement"),
    ],
)
def test_invalid_coverage_or_unsupported_claim_shape_is_rejected(graph, change, message):
    packet = request(graph)
    result = answer(packet)
    change(result)
    with pytest.raises(ValueError, match=message):
        validate_result(packet, result, graph)


def test_packages_require_all_delivery_comparisons_without_forcing_a_split(graph):
    packet = request(graph, "packages")
    result = answer(packet)
    result["findings"][0]["options"].pop()
    with pytest.raises(ValueError, match="delivery choices"):
        validate_result(packet, result, graph)
    result = answer(packet)
    result["findings"][0]["placement"] = "in-place"
    validate_result(packet, result, graph)
    validate_result(packet, answer(packet, finding=False), graph)


def test_canonical_results_preserve_the_contract_and_drop_unrelated_payload(graph):
    packet = request(graph)
    result = answer(packet)
    result["raw_source"] = "must not be retained"
    result["findings"][0]["raw_source"] = "must not be retained"
    result["findings"][0]["options"][0]["raw_source"] = "must not be retained"
    canonical = canonical_result(packet, result)
    assert "raw_source" not in canonical
    assert "raw_source" not in canonical["findings"][0]
    assert "raw_source" not in canonical["findings"][0]["options"][0]
    validate_result(packet, canonical, graph)


def test_reconciliation_requires_every_module_lens_pair_once(graph):
    rows = [row(packet := request(graph, lens), answer(packet, finding=False)) for lens in LENSES]
    summary = reconcile_architecture(set(request(graph)["scope"]), rows)
    assert summary["assessed_pairs"] == summary["expected_pairs"] == 6
    assert summary["completed_lenses"] == list(LENSES)
    assert summary["outcomes"] == {"keep": 6}
    for malformed in (rows[:-1], rows + rows[:1]):
        with pytest.raises(ValueError, match="module/lens"):
            reconcile_architecture(set(request(graph)["scope"]), malformed)
    unrelated = copy.deepcopy(rows)
    unrelated[0]["result"]["result"]["lens"] = "packages"
    with pytest.raises(ValueError, match="lens"):
        reconcile_architecture(set(request(graph)["scope"]), unrelated)


def test_reconciliation_reports_unknown_coverage_and_empty_primary_scope(graph):
    assert reconcile_architecture(set(), [])["scope_status"] == "no_eligible_modules"
    rows = [row(packet := request(graph, lens), answer(packet, finding=False)) for lens in LENSES]
    rows[0]["result"]["result"]["assessments"][0]["outcome"] = "unknown"
    summary = reconcile_architecture(set(request(graph)["scope"]), rows)
    assert summary["outcomes"] == {"unknown": 1, "keep": 5}
