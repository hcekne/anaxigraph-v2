"""Observed boundaries guide component changes without claiming runtime guarantees."""

from __future__ import annotations

import gc
import json
import weakref
from dataclasses import replace

from anaxigraph.analysis.changes import findings_delta
from anaxigraph.analysis.charter import CHARTER, _cycles, module_facts
from anaxigraph.analysis.guidance import structural_findings
from anaxigraph.application import Application
from anaxigraph.contracts import Edge
from anaxigraph.indexing import index


def write(root, path, source):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)


def module(graph, path):
    return next(node for node in graph.nodes if node.kind == "module" and node.path == path)


def test_declared_public_contract_allows_explicit_underscore_export(tmp_path):
    write(
        tmp_path,
        "owner.py",
        "__all__ = ['_entry']\ndef _entry():\n    return 1\ndef hidden():\n    return 2\n",
    )
    write(tmp_path, "client.py", "from owner import _entry, hidden\n_entry()\nhidden()\n")
    graph = index(tmp_path)
    surface = module_facts(graph, module(graph, "owner.py"))["public_surface"]
    assert surface == {
        "basis": "literal-__all__",
        "count": 1,
        "names": ["_entry"],
        "truncated": False,
    }
    symbols = {
        node.name: node
        for node in graph.nodes
        if node.path == "owner.py" and node.kind == "function"
    }
    assert symbols["_entry"].data["public"] is True
    assert symbols["hidden"].data["public"] is False
    facts = module_facts(graph, module(graph, "client.py"))
    assert facts["private_access_count"] == 2
    assert {item["target"] for item in facts["private_accesses"]} == {"symbol:owner.py#hidden"}
    assert {item["line"] for item in facts["private_accesses"]} == {1, 3}
    finding = next(
        item for item in structural_findings(graph) if item["kind"] == "private-interface-access"
    )
    assert finding["entity"] == "module:client.py" and finding["status"] == "concern"


def test_dynamic_exports_are_unknown_and_surfaces_do_not_create_a_size_gate(tmp_path):
    source = "__all__ = ['first']\n__all__.append('second')\ndef first():\n    pass\ndef second():\n    pass\n"
    write(tmp_path, "dynamic.py", source)
    write(
        tmp_path, "broad.py", "\n".join(f"def operation_{i}():\n    return {i}" for i in range(40))
    )
    graph = index(tmp_path)
    assert module(graph, "dynamic.py").data["public_surface"]["basis"] == "dynamic-__all__"
    assert module_facts(graph, module(graph, "broad.py"))["public_surface"]["count"] == 40
    assert structural_findings(graph) == []


def test_public_surface_is_bounded_and_future_directives_are_not_exports(tmp_path):
    write(
        tmp_path,
        "many.py",
        "from __future__ import annotations\n" + "\n".join(f"value_{i} = {i}" for i in range(120)),
    )
    graph = index(tmp_path)
    surface = module(graph, "many.py").data["public_surface"]
    assert surface["count"] == 120
    assert len(surface["names"]) == 100 and surface["truncated"]
    assert "annotations" not in surface["names"]


def test_star_import_does_not_pretend_its_public_surface_is_known(tmp_path):
    write(tmp_path, "exports.py", "from unknown_provider import *\n")
    surface = module(index(tmp_path), "exports.py").data["public_surface"]
    assert surface["basis"] == "unresolved-star-import"
    assert surface["count"] is None and "*" not in surface["names"]


def test_dependency_hash_tracks_public_contract_not_body_or_unrelated_edits(tmp_path):
    write(tmp_path, "owner.py", "def send(value: str) -> str:\n    return value\n")
    write(tmp_path, "client.py", "from owner import send\ndef run():\n    return send('hello')\n")
    first = index(tmp_path)
    expected = module_facts(first, module(first, "client.py"))
    write(
        tmp_path,
        "owner.py",
        "# new comment\ndef send(value: str) -> str:\n    return value.upper()\n",
    )
    write(tmp_path, "unrelated.py", "def other():\n    return 1\n")
    second = index(tmp_path)
    assert module_facts(second, module(second, "client.py")) == expected
    write(
        tmp_path, "owner.py", "def send(value: str, retries: int = 1) -> str:\n    return value\n"
    )
    third = index(tmp_path)
    assert (
        module_facts(third, module(third, "client.py"))["dependency_interface_fingerprint"]
        != expected["dependency_interface_fingerprint"]
    )


def test_component_tests_and_consumer_checks_are_distinct_observations(tmp_path):
    write(tmp_path, "store.py", "def save(value):\n    return value\n")
    write(
        tmp_path, "api.py", "from store import save\ndef submit(value):\n    return save(value)\n"
    )
    write(
        tmp_path,
        "tests/test_store.py",
        "from store import save\ndef test_saved():\n    assert save(1) == 1\n",
    )
    write(
        tmp_path,
        "tests/test_api.py",
        "from api import submit\ndef test_submitted():\n    assert submit(1) == 1\n",
    )
    graph = index(tmp_path)
    facts = module_facts(graph, module(graph, "store.py"))
    assert facts["observed_test_paths"] == ["tests/test_store.py"]
    assert facts["consumer_test_paths"] == ["tests/test_api.py"]
    assert "do not establish behavioral coverage" in facts["limitations"]
    application = Application(tmp_path)
    application.scan()
    brief = application.prepare_change("change save")
    assert brief["implementation_advice"]["principles_ref"] == "charter"
    assert len(application.inspect("charter")["entity"]["data"]["principles"]) == len(CHARTER) == 11
    assert "principles" not in brief["implementation_advice"]
    check = next(
        item
        for item in brief["implementation_advice"]["steps"]
        if item["kind"] == "compatibility-and-tests"
    )
    assert check["component_tests"] == ["tests/test_store.py"]
    assert check["consumer_tests"] == ["tests/test_api.py"]
    assert "required project checks" in check["suggestion"]
    assert (
        application.assess_plan("change save", ["store.py"])["brief"]["implementation_advice"][
            "principles_ref"
        ]
        == brief["implementation_advice"]["principles_ref"]
    )
    assert len(json.dumps(brief, indent=2).encode()) <= 8000


def test_observed_cycles_have_witnesses_without_guessing_layer_rules(tmp_path):
    write(tmp_path, "storage.py", "from ui import render\ndef load():\n    return render()\n")
    write(tmp_path, "ui.py", "from storage import load\ndef render():\n    return load()\n")
    graph = index(tmp_path)
    facts = module_facts(graph, module(graph, "storage.py"))
    assert facts["cycle_size"] == 2 and facts["cycle_paths"] == ["storage.py", "ui.py"]
    assert facts["cycle_evidence"][0]["path"] == "storage.py"
    assert "No layer direction rule" in facts["limitations"]
    findings = [item for item in structural_findings(graph) if item["kind"] == "dependency-cycle"]
    assert {item["entity"] for item in findings} == {"module:storage.py", "module:ui.py"}
    write(tmp_path, "ui.py", "def render():\n    return 1\n")
    acyclic = index(tmp_path)
    assert not structural_findings(acyclic)


def test_ambiguous_edges_do_not_become_private_access_claims(tmp_path):
    write(tmp_path, "owner.py", "def _hidden():\n    return 1\n")
    write(tmp_path, "client.py", "pass\n")
    graph = index(tmp_path)
    changed = replace(
        graph,
        edges=(
            *graph.edges,
            Edge(
                "module:client.py", "symbol:owner.py#_hidden", "calls", "client.py", 1, "ambiguous"
            ),
        ),
    )
    assert module_facts(changed, module(changed, "client.py"))["private_access_count"] == 0
    assert structural_findings(changed) == []


def test_resolved_private_class_helpers_are_boundary_observations(tmp_path):
    write(
        tmp_path,
        "owner.py",
        "class Service:\n    @staticmethod\n    def _helper():\n        return 1\n",
    )
    write(tmp_path, "client.py", "from owner import Service\nService._helper()\n")
    graph = index(tmp_path)
    facts = module_facts(graph, module(graph, "client.py"))
    assert facts["private_access_count"] == 1
    assert facts["private_accesses"][0]["target"] == "symbol:owner.py#Service._helper"
    method_graph = replace(
        graph,
        nodes=tuple(
            replace(node, kind="method") if node.id.endswith("#Service._helper") else node
            for node in graph.nodes
        ),
    )
    assert (
        module_facts(method_graph, module(method_graph, "client.py"))["private_access_count"] == 1
    )


def test_private_access_delta_keeps_stable_identity_and_measured_growth(tmp_path):
    write(tmp_path, "owner.py", "def _hidden():\n    return 1\n")
    write(tmp_path, "client.py", "from owner import _hidden\n")
    first = index(tmp_path)
    write(tmp_path, "client.py", "from owner import _hidden\n_hidden()\n")
    second = index(tmp_path)
    finding = next(
        item for item in findings_delta(second, first) if item["kind"] == "private-interface-access"
    )
    assert finding["entity"] == "module:client.py"
    assert (
        finding["change"] == "worsened"
        and finding["previous_measured"] == 1
        and finding["measured"] == 2
    )


def test_large_dependency_chains_are_iterative_and_cache_does_not_retain_graph(tmp_path):
    adjacency = {str(i): {str(i + 1)} for i in range(2000)} | {"2000": set()}
    assert _cycles(adjacency) == {}
    adjacency["2000"].add("0")
    assert len(_cycles(adjacency)["0"]) == 2001
    write(tmp_path, "owner.py", "def deliver():\n    return 1\n")
    graph = index(tmp_path)
    owner = module(graph, "owner.py")
    facts = module_facts(graph, owner)
    facts["public_surface"]["names"].clear()
    assert module_facts(graph, owner)["public_surface"]["names"] == ["deliver"]
    reference = weakref.ref(graph)
    del graph, owner
    gc.collect()
    assert reference() is None
