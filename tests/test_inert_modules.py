"""Inert source markers need honest empty capabilities, without fabricated behavior."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace

import pytest
from test_workflows import answer, app

from anaxigraph.analysis import build_request, plan_scopes
from anaxigraph.application import Application
from anaxigraph.indexing import discovery, index
from anaxigraph.workflows.engine import WorkflowRunner


@pytest.mark.parametrize("filename", ["__init__.py", "ordinary.py"])
@pytest.mark.parametrize(
    "source,inert",
    [
        ("", True),
        ("# A namespace marker.\n", True),
        ('"""A package marker."""\n', True),
        ('"""A package marker."""\npass\n', True),
        ("pass\n", True),
        ("import os\n", False),
        ("from another import action\n", False),
        ("value = 1\n", False),
        ("def operation():\n    pass\n", False),
        ("class Interface:\n    pass\n", False),
        ("print('an observable side effect')\n", False),
        ("if False:\n    pass\n", False),
    ],
)
def test_inert_is_an_observed_syntax_fact_not_a_filename_rule(tmp_path, filename, source, inert):
    (tmp_path / filename).write_text(source)
    module = next(node for node in index(tmp_path).nodes if node.kind == "module")
    assert module.data["inert_module"] is inert
    assert module.data["basis"] == "observed"


@pytest.mark.parametrize("capabilities", [[], None, "missing"])
@pytest.mark.parametrize("inert", [False, True])
def test_only_observed_inert_modules_accept_an_empty_capability_list(tmp_path, capabilities, inert):
    (tmp_path / "module.py").write_text('"""Marker."""\n' if inert else "value = 1\n")
    application = Application(tmp_path)
    application.scan()
    request = application.analysis_request("annotate")
    result = answer(request)
    annotation = result["annotations"][0]
    if capabilities == "missing":
        annotation.pop("capabilities")
    else:
        annotation["capabilities"] = capabilities
    if inert:
        assert request["response_schema"]["annotations"][0]["capabilities"] == []
        assert request["response_schema"]["annotations"][0]["functions"] == []
        assert request["response_schema"]["annotations"][0]["contracts"] == []
        assert "never null" in request["packet"]["instruction"]
    if inert and capabilities == []:
        assert application.submit_analysis(request["request_id"], result)["saved"]
    else:
        with pytest.raises(ValueError, match="capabilities"):
            application.submit_analysis(request["request_id"], result)


def marker_answer(request):
    result = answer(request)
    if request["kind"] == "annotate":
        inert = {
            node["id"] for node in request["packet"]["modules"] if node["data"]["inert_module"]
        }
        for annotation in result["annotations"]:
            if annotation["node_id"] in inert:
                annotation.update(
                    overview="Marks a package namespace without executable behavior.",
                    detail="Contains documentation only, without functions or runtime statements.",
                    capabilities=[],
                    functions=[],
                    contracts=[],
                )
    return result


def test_whole_mapping_patterns_and_fresh_eyes_cover_marker_without_inventing_requirements(
    tmp_path,
):
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text('"""Package namespace marker."""\n')
    (package / "application.py").write_text("def deliver():\n    return 1\n")
    application = Application(tmp_path)
    calls = Counter()

    def provider(request, _worker, **_kwargs):
        calls[request["kind"]] += 1
        if request["kind"] == "fresh-eyes":
            assert len(request["packet"]["requirements"]) == 1
            assert "namespace" not in json.dumps(request["packet"]).lower()
            assert "__init__.py" not in json.dumps(request["packet"])
        return marker_answer(request)

    runner = WorkflowRunner(application, provider)
    mapping = runner.start("mapping", background=False)
    assert mapping["status"] == "completed", mapping
    assert mapping["reconciliation"]["annotated_modules"] == 2
    patterns = runner.start(
        "patterns",
        {"pattern_mode": "exhaustive"},
        scope=["package/__init__.py", "package/application.py"],
        background=False,
    )
    assert patterns["status"] == "completed", patterns
    assert patterns["reconciliation"]["evaluated_pairs"] == 266
    assert patterns["reconciliation"]["architecture"]["outcomes"] == {"keep": 6}
    assert plan_scopes(application._current(), "architecture") == [
        ["module:package/application.py"]
    ]
    fresh = runner.start("fresh-eyes", background=False)
    assert fresh["status"] == "completed", fresh
    assert fresh["reconciliation"]["declared_modules"] == 2
    assert fresh["reconciliation"]["alternatives"] == 3
    assert calls == {
        "annotate": 2,
        "patterns": 2,
        "architecture": 3,
        "delivery": 1,
        "fresh-eyes": 2,
    }


def test_inert_only_tail_batches_join_behavioral_scope_without_adding_requirements(tmp_path):
    application = app(tmp_path, count=2)
    (tmp_path / "zz_marker.py").write_text("pass\n")
    runner = WorkflowRunner(application, lambda request, *_args, **_kwargs: marker_answer(request))
    assert runner.start("mapping", background=False)["status"] == "completed"
    graph = application._current()
    marker = next(node for node in graph.nodes if node.path == "zz_marker.py")
    marker.data["annotation"]["capabilities"] = ["Defines an organizational placeholder."]
    batches = plan_scopes(graph, "fresh-eyes", batch_size=1)
    assert len(batches) == 2
    assert sum(len(batch) for batch in batches) == 3
    for batch in batches:
        packet = build_request(graph, "fresh-eyes", batch)["packet"]
        assert len(packet["requirements"]) == 1


def test_index_fact_upgrade_preserves_nine_compatible_saved_annotations(tmp_path, monkeypatch):
    import anaxigraph.indexing as indexing

    parse_module = indexing.parse_module
    current_version = discovery.INDEX_VERSION

    def legacy_parser(*args):
        parsed, node, error = parse_module(*args)
        return (
            parsed,
            replace(node, data={k: v for k, v in node.data.items() if k != "inert_module"}),
            error,
        )

    monkeypatch.setattr(discovery, "INDEX_VERSION", "python-ast-v1")
    monkeypatch.setattr(indexing, "parse_module", legacy_parser)
    original = app(tmp_path, count=9)
    calls = []

    def provider(request, *_args, **_kwargs):
        calls.append(request["request_id"])
        return answer(request)

    assert (
        WorkflowRunner(original, provider).start("mapping", background=False)["status"]
        == "completed"
    )
    prior_snapshot = original.status()["snapshot"]["id"]
    assert original.status()["annotations"]["current"] == 9
    monkeypatch.setattr(discovery, "INDEX_VERSION", current_version)
    monkeypatch.setattr(indexing, "parse_module", parse_module)
    refreshed = Application(tmp_path)
    scanned = refreshed.scan()
    assert scanned["snapshot"]["id"] != prior_snapshot
    assert scanned["annotations"]["current"] == 9
    assert all(
        node.data.get("inert_module") is False
        for node in refreshed._current().nodes
        if node.kind == "module"
    )
    rerun = WorkflowRunner(refreshed, provider).start("mapping", background=False)
    assert rerun["status"] == "completed", rerun
    assert rerun["total_batches"] == 0
    assert len(calls) == 9
