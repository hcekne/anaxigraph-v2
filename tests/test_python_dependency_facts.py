"""Adversarial syntax and static type/import dependencies never execute source."""

from __future__ import annotations

import pytest

from anaxigraph.indexing import IndexSession, index


def write(root, path, source):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)


@pytest.mark.parametrize("in_function", [False, True])
def test_deep_expression_failure_is_contained_to_one_explicit_error_module(tmp_path, in_function):
    expression = " + ".join(["'term'"] * 500)
    source = f"def deep():\n    return {expression}\n" if in_function else f"value = {expression}\n"
    write(tmp_path, "deep.py", source)
    write(tmp_path, "healthy.py", "def live():\n    return 1\nlive()\n")
    session = IndexSession(tmp_path)
    graph = session.scan()
    deep = next(node for node in graph.nodes if node.id == "module:deep.py")
    assert deep.data["parse_status"] == "error"
    assert any("deep.py: RecursionError:" in item for item in graph.diagnostics)
    assert "deep.py" in graph.snapshot.files
    assert not any(node.path == "deep.py" and node.kind != "module" for node in graph.nodes)
    assert any(
        edge.kind == "calls" and edge.target == "symbol:healthy.py#live" for edge in graph.edges
    )
    assert session.scan() == graph


def test_function_and_field_annotations_link_mapped_types_with_future_and_forward_strings(tmp_path):
    write(tmp_path, "models.py", "class User:\n    pass\nclass Error:\n    pass\n")
    write(
        tmp_path,
        "app.py",
        "from __future__ import annotations\n"
        "import models\nfrom models import User as Person\n"
        "class Store:\n    current: 'models.User | None'\n"
        "async def fetch(Person: Person, /, *items: 'list[models.User]', "
        "err: 'models.Error', **extra: 'Person') -> 'tuple[Person, models.Error]':\n"
        "    return (Person, err)\n",
    )
    graph = index(tmp_path)
    references = [edge for edge in graph.edges if edge.kind == "references"]
    target_types = {"symbol:models.py#User", "symbol:models.py#Error"}
    assert {
        edge.target for edge in references if edge.source == "symbol:app.py#fetch"
    } >= target_types
    assert all(
        edge.resolution == "resolved"
        for edge in references
        if edge.source == "symbol:app.py#fetch" and edge.target in target_types
    )
    assert any(
        edge.source == "symbol:app.py#Store" and edge.target == "symbol:models.py#User"
        for edge in references
    )
    assert not any("__future__" in node.id for node in graph.nodes)
    ids = {node.id for node in graph.nodes}
    assert all(edge.source in ids and edge.target in ids for edge in graph.edges)


def test_type_values_and_annotation_factories_are_not_confused_with_forward_types(tmp_path):
    marker = tmp_path / "executed"
    write(
        tmp_path,
        "app.py",
        "from typing import Literal, Annotated\nclass Customer:\n    pass\n"
        "class Store:\n    pass\n"
        f"def make_type(value):\n    open({str(marker)!r}, 'w').write(value)\n"
        "def label(value: Literal['Customer']) -> str:\n    return value\n"
        "def save(value: Annotated['Store', 'Customer']) -> 'Store':\n    return value\n"
        "def factory(value: make_type('Customer')):\n    return value\n",
    )
    graph = index(tmp_path)
    assert not marker.exists()
    references = [edge for edge in graph.edges if edge.kind == "references"]
    assert not any(
        edge.target == "symbol:app.py#Customer"
        and edge.source in {"symbol:app.py#label", "symbol:app.py#save", "symbol:app.py#factory"}
        for edge in references
    )
    assert any(
        edge.source == "symbol:app.py#save" and edge.target == "symbol:app.py#Store"
        for edge in references
    )
    assert not any(
        edge.kind == "calls" and edge.source == "symbol:app.py#factory" for edge in graph.edges
    )


@pytest.mark.parametrize(
    "selection",
    [
        "if selected:\n    from first import run as execute\nelse:\n    from second import run as execute\n",
        "try:\n    from first import run as execute\nexcept ImportError:\n    from second import run as execute\n",
        "if selected:\n    import first as provider\nelse:\n    import second as provider\n",
    ],
)
def test_conditional_import_choices_survive_as_ambiguous_edges(tmp_path, selection):
    write(tmp_path, "first.py", "def run():\n    return 1\n")
    write(tmp_path, "second.py", "def run():\n    return 2\n")
    call = "provider.run" if "as provider" in selection else "execute"
    write(tmp_path, "app.py", selection + f"def use():\n    return {call}()\n")
    graph = index(tmp_path)
    calls = [edge for edge in graph.edges if edge.kind == "calls"]
    assert {(edge.target, edge.resolution) for edge in calls} == {
        ("symbol:first.py#run", "ambiguous"),
        ("symbol:second.py#run", "ambiguous"),
    }
    imports = [edge for edge in graph.edges if edge.kind == "imports"]
    assert len(imports) == 2 and all(edge.resolution == "resolved" for edge in imports)
    assert imports[0].target != imports[1].target


def test_single_conditional_import_can_be_unbound_and_is_not_uniquely_resolved(tmp_path):
    write(tmp_path, "first.py", "def run():\n    return 1\n")
    write(tmp_path, "app.py", "if selected:\n    from first import run\nrun()\n")
    calls = [edge for edge in index(tmp_path).edges if edge.kind == "calls"]
    assert len(calls) == 1
    assert calls[0].target == "symbol:first.py#run" and calls[0].resolution == "ambiguous"


def test_unresolved_conditional_alternative_remains_visible_beside_the_known_candidate(tmp_path):
    write(tmp_path, "first.py", "def run():\n    return 1\n")
    write(
        tmp_path,
        "app.py",
        "if selected:\n    from first import run\nelse:\n    from missing import run\nrun()\n",
    )
    graph = index(tmp_path)
    calls = [edge for edge in graph.edges if edge.kind == "calls"]
    assert len(calls) == 2 and all(edge.resolution == "ambiguous" for edge in calls)
    assert any(edge.target == "symbol:first.py#run" for edge in calls)
    assert any(edge.target.startswith("external:") for edge in calls)
