"""Owner search uses distinct modules, captured source and honestly current interpretations."""

from __future__ import annotations

from dataclasses import replace

import pytest

from anaxigraph.analysis.guidance import prepare
from anaxigraph.analysis.retrieval import rank_owners
from anaxigraph.application import Application
from anaxigraph.contracts import Graph, Node, Snapshot


def fixture_graph(files, annotations=None):
    modules = []
    for path in files:
        data = (annotations or {}).get(path, {})
        modules.append(Node("module:" + path, "module", path[:-3], path, data=data))
    snapshot = Snapshot(
        "snapshot", "/fixture", "revision", False, dict.fromkeys(files, "abc"), "now"
    )
    return Graph(snapshot, tuple(modules), ())


def test_source_words_find_unmapped_owner_without_project_execution(tmp_path):
    (tmp_path / "opaque.py").write_text(
        "raise RuntimeError('analyzed projects must never execute')\n"
        "def deliver():\n    return 'quasar reconciliation'\n"
    )
    (tmp_path / "other.py").write_text("def unrelated():\n    return None\n")
    app = Application(tmp_path)
    app.scan()
    brief = app.prepare_change("Improve quasar reconciliation")
    owner = brief["likely_owners"][0]
    assert owner["path"] == "opaque.py"
    assert owner["score"] > 0
    assert any(reason.startswith("source:") for reason in owner["match_reasons"])
    assert owner["annotation_status"] == "missing"
    assert brief["baseline_id"] == brief["snapshot_id"]
    assert app.indexer.sources.files_read == 0
    app.indexer.sources.maximum_bytes = 1
    assert app.prepare_change("quasar reconciliation")["likely_owners"][0]["path"] == "opaque.py"
    (tmp_path / "opaque.py").write_text("# unscanned edit\n")
    with pytest.raises(ValueError, match="changed after mapping"):
        app.prepare_change("quasar reconciliation")


def test_exact_symbol_and_path_outweigh_lexical_noise_and_include_witness():
    sources = {
        "noise.py": "invoice rounding export tenant account csv",
        "opaque.py": "def emit_csv():\n    pass\n",
        "exact.py": "def unrelated():\n    pass\n",
    }
    graph = fixture_graph(sources)
    symbol = Node("function:opaque.py:emit_csv", "function", "emit_csv", "opaque.py", 9)
    graph = replace(graph, nodes=(*graph.nodes, symbol))
    rows = rank_owners(graph, sources, "Fix emit_csv invoice rounding export tenant account csv")
    assert rows[0]["path"] == "opaque.py"
    assert rows[0]["match_scores"]["exact_symbol"] == 20
    assert rows[0]["evidence"] == [{"path": "opaque.py", "line": 9}]
    assert (
        rank_owners(graph, sources, "Change exact.py invoice rounding export")[0]["path"]
        == "exact.py"
    )
    brief = prepare(graph, rows, "Fix emit_csv")
    assert brief["implementation_advice"]["steps"][0]["evidence"][0]["line"] == 9


def test_ordinary_english_verb_does_not_override_relevant_source_and_symbols():
    sources = {
        "audit.py": "def record(value):\n    pass\n",
        "invoices.py": "def export_csv(invoice, payments):\n    return invoice, payments\n",
    }
    graph = fixture_graph(sources)
    symbols = (
        Node("function:audit.py:record", "function", "record", "audit.py"),
        Node("function:invoices.py:export_csv", "function", "export_csv", "invoices.py"),
    )
    graph = replace(graph, nodes=(*graph.nodes, *symbols))
    rows = rank_owners(graph, sources, "Record invoice payments with CSV export")
    assert rows[0]["path"] == "invoices.py"
    assert (
        next(row for row in rows if row["path"] == "audit.py")["match_scores"]["exact_symbol"] == 2
    )


def test_explicit_test_file_can_own_a_test_change_without_test_helpers_leading_feature_work(
    tmp_path,
):
    (tmp_path / "billing.py").write_text("def invoice():\n    return 'quasar invoice'\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_billing.py").write_text("def test_invoice():\n    assert 'quasar invoice'\n")
    app = Application(tmp_path)
    app.scan()
    assert (
        app.prepare_change("Fix tests/test_billing.py")["likely_owners"][0]["path"]
        == "tests/test_billing.py"
    )
    assert app.prepare_change("Improve quasar invoice")["likely_owners"][0]["path"] == "billing.py"


def test_nested_path_basename_is_an_exact_match_without_matching_partial_names():
    sources = {"package/opaque.py": "pass", "other.py": "invoice export tenant"}
    graph = fixture_graph(sources)
    assert (
        rank_owners(graph, sources, "Fix opaque.py invoice export")[0]["path"]
        == "package/opaque.py"
    )
    assert rank_owners(graph, sources, "Fix paque.py") == []


def test_module_aggregation_keeps_diverse_owners_before_limiting():
    sources = {path: "quasar reconciliation" for path in ("a.py", "b.py", "c.py")}
    graph = fixture_graph(sources)
    symbols = tuple(
        Node(
            f"function:a.py:reconcile_{index}", "function", f"reconcile_{index}", "a.py", index + 1
        )
        for index in range(100)
    )
    graph = replace(graph, nodes=(*graph.nodes, *symbols))
    assert {row["path"] for row in rank_owners(graph, sources, "quasar reconciliation", 3)} == set(
        sources
    )


@pytest.mark.parametrize("field", ["detail", "capabilities", "functions", "contracts"])
def test_current_semantic_fields_find_behavior_but_stale_interpretations_do_not_rank(field):
    values = {
        "detail": "quasar reconciliation",
        "capabilities": ["quasar reconciliation"],
        "functions": [{"overview": "quasar reconciliation"}],
        "contracts": [{"statement": "quasar reconciliation"}],
    }
    source = {"opaque.py": "pass\n"}
    annotation = {"annotation_status": "current", "annotation": {field: values[field]}}
    graph = fixture_graph(source, {"opaque.py": annotation})
    rows = rank_owners(graph, source, "quasar reconciliation")
    assert rows[0]["match_scores"]["current_annotation"] > 0
    stale = replace(graph.nodes[0], data=annotation | {"annotation_status": "stale"})
    graph = replace(graph, nodes=(stale,))
    assert rank_owners(graph, source, "quasar reconciliation") == []
    rows = rank_owners(graph, {"opaque.py": "# quasar reconciliation"}, "quasar reconciliation")
    assert rows[0]["annotation_status"] == "stale"
    assert "current_annotation" not in rows[0]["match_scores"]


def test_rich_annotations_preserve_three_owners_and_their_essential_contracts():
    sources = dict.fromkeys(("a.py", "b.py", "c.py"), "invoice export")
    annotations = {
        path: {
            "annotation_status": "current",
            "annotation": {
                "overview": "Invoice export behavior " * 100,
                "contracts": [
                    {
                        "statement": f"Preserve {path} tenant isolation. "
                        + "Essential behavior " * 100,
                        "basis": "declared",
                        "evidence": [{"path": path, "line": 1}],
                    }
                ]
                * 30,
            },
        }
        for path in sources
    }
    graph = fixture_graph(sources, annotations)
    rows = rank_owners(graph, sources, "invoice export")
    brief = prepare(graph, rows, "invoice export " * 200)
    assert [row["path"] for row in brief["likely_owners"]] == list(sources)
    assert {row["owner"] for row in brief["contracts"]} == {node.id for node in graph.nodes}
    assert all(
        row["evidence"] and "tenant isolation" in row["statement"] for row in brief["contracts"]
    )
    assert brief["contract_overflow"] == 87
    assert brief["context_truncated"] and brief["task_truncated"]


def test_concise_test_list_prioritizes_owner_specific_tests_over_alphabetical_smoke_tests(tmp_path):
    (tmp_path / "billing.py").write_text("def invoice():\n    return 'quasar invoice'\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in ("a_smoke", "b_smoke", "c_smoke", "test_billing"):
        (tests / f"{name}.py").write_text(
            "from billing import invoice\ndef test_invoice():\n    assert invoice()\n"
        )
    app = Application(tmp_path)
    app.scan()
    brief = app.prepare_change("Improve quasar invoice")
    assert brief["tests_total"] == 4
    assert brief["tests"][0] == "tests/test_billing.py"
    assert [owner["path"] for owner in brief["likely_owners"]] == ["billing.py"]
