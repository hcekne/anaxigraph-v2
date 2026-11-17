"""Changing analysis scope must not masquerade as repairing the architecture."""

from dataclasses import replace

import pytest

from anaxigraph.analysis import charter
from anaxigraph.analysis.architecture_verification import boundary_delta
from anaxigraph.analysis.changes import findings_delta
from anaxigraph.application import Application
from anaxigraph.contracts import Edge, Graph, Node, Snapshot


def cycle_graph(count=3):
    paths = [f"part_{number:02}.py" for number in range(count)]
    nodes = tuple(
        node
        for path in paths
        for node in (
            Node(f"module:{path}", "module", path, path, data={"is_test": False}),
            Node(f"symbol:{path}#_private", "function", "_private", path),
        )
    )
    edges = tuple(
        Edge(f"module:{path}", f"symbol:{paths[(i + 1) % count]}#_private", "calls", path)
        for i, path in enumerate(paths)
    )
    return Graph(
        Snapshot("baseline", "/fixture", None, False, dict.fromkeys(paths, "same"), "now"),
        nodes,
        edges,
    )


def _as_tests(graph, paths):
    return replace(
        graph,
        snapshot=replace(graph.snapshot, id="test-scope"),
        nodes=tuple(
            replace(node, data=node.data | {"is_test": True})
            if node.kind == "module" and node.path in paths
            else node
            for node in graph.nodes
        ),
    )


@pytest.mark.parametrize("entering", [False, True])
@pytest.mark.parametrize("count", [3, 26])
def test_role_transition_includes_indirect_cycle_changes_without_claiming_repairs(entering, count):
    production = cycle_graph(count)
    scoped = _as_tests(production, {f"part_{count - 1:02}.py"})
    before, after = (scoped, production) if entering else (production, scoped)
    result = boundary_delta(after, before, full=True)
    cycle = [item for item in result["findings"] if item["kind"] == "dependency-cycle"]
    assert len(cycle) == count
    assert all(
        item["change"] == "scope_changed" and item["status"] == "unresolved" for item in cycle
    )
    assert result["rescoped_paths"] == [f"part_{count - 1:02}.py"]
    assert result["rescoped_total"] == 1
    direct = [item for item in result["findings"] if item["path"] == f"part_{count - 1:02}.py"]
    assert {item["kind"] for item in direct} == {
        "dependency-cycle",
        "private-interface-access",
        "shared-consumers",
    }
    assert all(item["change"] == "scope_changed" for item in direct)
    assert production.snapshot.files == scoped.snapshot.files
    assert production.edges == scoped.edges


def test_shrinking_but_persistent_cycle_is_not_an_improvement_in_top_level_findings():
    before = cycle_graph()
    before = replace(
        before,
        edges=before.edges
        + (Edge("module:part_01.py", "module:part_00.py", "imports", "part_01.py"),),
    )
    after = _as_tests(before, {"part_02.py"})
    findings = [
        item for item in findings_delta(after, before) if item["kind"] == "dependency-cycle"
    ]
    assert len(findings) == 2
    assert all(item["change"] == "scope_changed" for item in findings)
    assert all(item["measured"] == 2 and item["previous_measured"] == 3 for item in findings)


def test_entering_production_retains_newly_relevant_private_access():
    after = cycle_graph()
    before = _as_tests(after, {"part_02.py"})
    findings = findings_delta(after, before)
    private = next(
        item
        for item in findings
        if item["kind"] == "private-interface-access" and item["entity"] == "module:part_02.py"
    )
    assert private["change"] == "scope_changed"
    assert private["status"] == "concern" and private["measured"] == 1


@pytest.mark.parametrize("added", [False, True])
def test_real_file_addition_or_deletion_preserves_actual_boundary_changes(added):
    complete = cycle_graph()
    partial = replace(
        complete,
        snapshot=replace(
            complete.snapshot,
            id="partial",
            files={
                path: value
                for path, value in complete.snapshot.files.items()
                if path != "part_02.py"
            },
        ),
        nodes=tuple(node for node in complete.nodes if node.path != "part_02.py"),
        edges=tuple(
            edge
            for edge in complete.edges
            if edge.path != "part_02.py" and "part_02.py" not in edge.target
        ),
    )
    before, after = (partial, complete) if added else (complete, partial)
    result = boundary_delta(after, before)
    assert result["rescoped_total"] == 0
    assert result["findings"]
    assert all(item["change"] != "scope_changed" for item in result["findings"])
    cycle = [item for item in result["findings"] if item["kind"] == "dependency-cycle"]
    assert len(cycle) == 3
    assert all(item["change"] == ("introduced" if added else "improved") for item in cycle)


def test_real_concerns_precede_many_scope_changes_and_full_output_is_available():
    before = cycle_graph(60)
    scoped = _as_tests(before, set(before.snapshot.files))
    nodes = (Node("module:zz_client.py", "module", "client", "zz_client.py"),)
    after = replace(
        scoped,
        snapshot=replace(scoped.snapshot, files=scoped.snapshot.files | {"zz_client.py": "added"}),
        nodes=scoped.nodes + nodes,
        edges=scoped.edges
        + (Edge("module:zz_client.py", "symbol:part_00.py#_private", "calls", "zz_client.py"),),
    )
    bounded = boundary_delta(after, before)
    full = boundary_delta(after, before, full=True)
    assert bounded["findings"][0]["path"] == "zz_client.py"
    assert bounded["findings"][0]["change"] == "introduced"
    assert bounded["truncated"] and len(bounded["findings"]) == 50
    assert not full["truncated"] and len(full["findings"]) == full["total"] == bounded["total"]
    assert bounded["rescoped_total"] == 60 and len(bounded["rescoped_paths"]) == 20
    assert len(full["rescoped_paths"]) == 60


def test_empty_role_transition_is_visible_without_inventing_boundary_changes():
    before = replace(cycle_graph(), edges=())
    after = _as_tests(before, {"part_02.py"})
    result = boundary_delta(after, before)
    assert result["findings"] == []
    assert result["rescoped_paths"] == ["part_02.py"]
    assert boundary_delta(before, before)["rescoped_total"] == 0


def test_shared_view_reuses_complete_cycles_and_keeps_public_facts_bounded(monkeypatch):
    before = cycle_graph(26)
    after = _as_tests(before, {"part_25.py"})
    calls = []
    original = charter._cycles

    def counted(adjacency):
        calls.append(len(adjacency))
        return original(adjacency)

    monkeypatch.setattr(charter, "_cycles", counted)
    for _ in range(3):
        boundary_delta(after, before)
        findings_delta(after, before)
        facts = charter.module_facts(before, before.nodes[0])
        assert facts["cycle_size"] == 26 and len(facts["cycle_paths"]) == 20
        facts["cycle_paths"].clear()
    assert calls == [26, 25]


def test_actual_role_edit_is_reported_through_application_validation(tmp_path):
    (tmp_path / "owner.py").write_text("def _private():\n    return 1\n")
    (tmp_path / "checks.py").write_text(
        "from owner import _private\ndef test_check():\n    assert _private() == 1\n"
    )
    app = Application(tmp_path)
    app.prepare_change("Update checks without changing production boundaries")
    (tmp_path / "checks.py").write_text(
        "import conftest\nfrom owner import _private\ndef test_check():\n    assert _private() == 1\n"
    )
    result = app.validate_change(full=True)["architecture"]["boundary_changes"]
    assert result["rescoped_paths"] == ["checks.py"]
    [private] = [item for item in result["findings"] if item["kind"] == "private-interface-access"]
    assert private["change"] == "scope_changed"
    assert private["status"] == "unresolved"


def test_application_full_validation_expands_scope_deltas_without_hiding_new_concerns(tmp_path):
    paths = [f"part_{number:02}.py" for number in range(60)]
    for i, path in enumerate(paths):
        (tmp_path / path).write_text(
            f"from part_{(i + 1) % len(paths):02} import _private\n"
            "def _private():\n    return 1\n"
            "def test_check():\n    assert _private() == 1\n"
        )
    app = Application(tmp_path)
    app.prepare_change("Correct test roles while keeping newly introduced production concerns")
    for path in paths:
        source = tmp_path / path
        source.write_text("import conftest\n" + source.read_text())
    (tmp_path / "zz_client.py").write_text("from part_00 import _private\n_private()\n")
    bounded = app.validate_change()["architecture"]["boundary_changes"]
    full = app.validate_change(full=True)["architecture"]["boundary_changes"]
    assert bounded["truncated"] and len(bounded["findings"]) == 50
    assert bounded["findings"][0]["path"] == "zz_client.py"
    assert bounded["findings"][0]["status"] == "concern"
    assert full["total"] == bounded["total"] > 50
    assert len(full["findings"]) == full["total"] and not full["truncated"]
    assert full["rescoped_paths"] == paths and full["rescoped_total"] == 60
