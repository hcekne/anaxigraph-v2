"""Derived views stay correct across comparisons, repeated identities and concurrent readers."""

import gc
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from anaxigraph.analysis import charter, extension
from anaxigraph.analysis.cache import graph_cached
from anaxigraph.analysis.guidance import structural_findings
from anaxigraph.contracts import Graph, Node, Snapshot


def graph(name):
    node = Node(f"module:{name}.py", "module", name, f"{name}.py", data={"loc": 601})
    return Graph(Snapshot("same-identity", "/fixture", None, False, {}, "now"), (node,), ())


def test_comparison_reuses_both_graphs_without_confusing_equal_snapshot_ids(monkeypatch):
    before, after = graph("before"), graph("after")
    builds = []
    original = charter._relations

    def derive(owner, modules):
        builds.append(owner.nodes[0].path)
        return original(owner, modules)

    monkeypatch.setattr(charter, "_relations", derive)
    for _ in range(4):
        for owner in (before, after):
            assert structural_findings(owner)[0]["entity"] == owner.nodes[0].id
            assert extension.check_reach(owner, [owner.nodes[0].path])["test_modules"] == 0
    assert builds == ["before.py", "after.py"]


def test_graph_views_have_a_fixed_bound_and_do_not_keep_graphs_alive():
    builds = []

    @graph_cached
    def paths(owner):
        result = tuple(node.path for node in owner.nodes)
        builds.append(result)
        return result

    first, second = graph("first"), graph("second")
    assert paths(first) == ("first.py",)
    assert paths(second) == ("second.py",)
    assert paths(first) == ("first.py",)
    third = graph("third")
    paths(third)
    paths(second)
    assert len(builds) == 3
    paths(first)
    assert len(builds) == 4  # A third build evicted the oldest built view.
    reference = weakref.ref(first)
    del first
    gc.collect()
    assert reference() is None


def test_simultaneous_readers_share_one_view_and_new_graph_objects_refresh_it():
    builds = []

    @graph_cached
    def names(owner):
        builds.append(owner.nodes[0].name)
        return tuple(node.name for node in owner.nodes)

    owner = graph("original")
    with ThreadPoolExecutor(8) as pool:
        assert list(pool.map(names, [owner] * 32)) == [("original",)] * 32
    changed = replace(owner, nodes=(replace(owner.nodes[0], name="updated"),))
    assert names(changed) == ("updated",)
    assert names(owner) == ("original",)
    assert builds == ["original", "updated"]
