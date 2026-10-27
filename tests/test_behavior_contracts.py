"""Catch behavior-field leaks before paid design, preserving safe saved understanding."""

from __future__ import annotations

import gc
import json
import shutil
import subprocess
import weakref
from dataclasses import replace

import pytest
from test_workflows import answer

from anaxigraph.analysis import behavior, validation
from anaxigraph.application import Application, core
from anaxigraph.workflows.engine import WorkflowRunner

LEAKS = {
    "part0.py": "Validate input using DemoResort.",
    "part1.py": "Return the bundled file from /data/seed.json unchanged.",
    "part2.py": "Preserve the SearchOptions request settings.",
    "part3.py": "Expose the fields in Resort.model_fields.",
}
CONFIG = {
    "pattern_mode": "exhaustive",
    "timeout_seconds": None,
    "stages": {
        kind: {"provider": "codex", "model": "fixture", "reasoning_effort": "xhigh"}
        for kind in ("annotate", "patterns", "fresh-eyes")
    },
}


def test_empty_root_package_name_does_not_match_ordinary_punctuation(tmp_path):
    (tmp_path / "__init__.py").write_text('"""Package marker."""\n')
    (tmp_path / "component.py").write_text("def send_result():\n    return 1\n")
    app = Application(tmp_path)
    app.scan()
    graph = app._current()
    behavior.check_behaviors(graph, ["Provide an observable result to a caller."])
    with pytest.raises(ValueError, match="source identity"):
        behavior.check_behaviors(graph, ["Invoke send_result() for the caller."])


def application(tmp_path):
    root = tmp_path / "original"
    root.mkdir()
    names = ["DemoResort", "DemoCatalogue", "SearchOptions", "Resort", "Store"]
    for i in range(10):
        declaration = f"class {names[i]}:\n    pass\n\n" if i < len(names) else ""
        (root / f"part{i}.py").write_text(declaration + "def status():\n    return 1\n")
    (root / "data").mkdir()
    (root / "data/seed.json").write_text('{"kind":"synthetic"}')
    app = Application(root)
    app.scan()
    return app


def result_for(request, *, legacy_leaks=False):
    result = answer(request)
    for annotation, module in zip(result["annotations"], request["packet"]["modules"]):
        annotation["overview"] = f"Defines source behavior in {module['path']}."
        annotation["capabilities"] = [
            "Reports status for each module and exports a record after each scan."
        ]
        annotation["contracts"] = [
            {
                "statement": LEAKS[module["path"]]
                if legacy_leaks and module["path"] in LEAKS
                else "Store records after validating input and preserve caller guarantees.",
                "basis": "inferred",
                "evidence": [{"path": module["path"], "line": 1}],
            }
        ]
        for function in annotation["functions"]:
            function["overview"] = f"Implements {function['node_id']}."
    return result


def legacy_application(tmp_path, monkeypatch, *, patterns=False):
    original = application(tmp_path)
    with monkeypatch.context() as old_boundary:
        old_boundary.setattr(validation, "check_annotation_behaviors", lambda *_args: None)
        old_boundary.setattr(core, "annotation_behavior_problems", lambda *_args: {})
        runner = WorkflowRunner(
            original,
            lambda request, *_args, **_kwargs: (
                result_for(request, legacy_leaks=True)
                if request["kind"] == "annotate"
                else answer(request)
            ),
        )
        assert runner.start("mapping", CONFIG, background=False)["status"] == "completed"
        if patterns:
            assert runner.start("patterns", CONFIG, background=False)["status"] == "completed"
    return Application(original.root, original.store.path)


@pytest.mark.parametrize("statement", list(LEAKS.values()))
@pytest.mark.parametrize("field", ["capabilities", "contracts"])
def test_new_annotations_reject_internal_identifiers_and_input_paths(tmp_path, statement, field):
    app = application(tmp_path)
    request = app.analysis_request("annotate", ["part0.py"])
    result = result_for(request)
    annotation = result["annotations"][0]
    if field == "contracts":
        annotation["contracts"][0]["statement"] = statement
    else:
        annotation["capabilities"] = [statement]
    with pytest.raises(ValueError, match=rf"{field}.*source identity"):
        app.submit_analysis(request["request_id"], result)
    assert app.status()["annotations"]["current"] == 0
    assert "contract statements" in request["packet"]["instruction"]
    assert "separate evidence field" in request["packet"]["instruction"]


def test_legacy_four_leaking_contracts_are_stale_and_only_those_modules_are_remapped(
    tmp_path, monkeypatch
):
    app = legacy_application(tmp_path, monkeypatch)
    modules = {node["path"]: node for node in app.modules()["items"]}
    assert app.status()["annotations"] == {"current": 6, "stale": 4, "missing": 0}
    assert {
        path for path, node in modules.items() if node["data"]["annotation_status"] == "stale"
    } == set(LEAKS)
    for path in LEAKS:
        data = app.inspect(f"module:{path}")["entity"]["data"]
        assert "contracts[0].statement" in data["annotation_stale_reason"]
        assert data["annotation"]["overview"] == f"Defines source behavior in {path}."
        assert all(
            "symbol:" in function["overview"] for function in data["annotation"]["functions"]
        )
        assert data["annotation"]["annotation_contract"] == "behavior-v2"
    with pytest.raises(ValueError, match="missing/stale"):
        app.analysis_request("fresh-eyes")

    calls = []

    def provider(request, *_args, **_kwargs):
        calls.extend(module["path"] for module in request["packet"]["modules"])
        return result_for(request)

    remapped = WorkflowRunner(app, provider).start("mapping", background=False)
    assert remapped["status"] == "completed", remapped
    assert remapped["total_batches"] == 4
    assert set(calls) == set(LEAKS)
    assert app.status()["annotations"] == {"current": 10, "stale": 0, "missing": 0}
    assert not any(node["data"].get("annotation_stale_reason") for node in app.modules()["items"])
    packet = json.dumps(app.analysis_request("fresh-eyes")["packet"])
    assert len(json.loads(packet)["requirements"]) == 20
    assert all(
        name not in packet for name in ["DemoResort", "SearchOptions", "part0.py", "data/seed.json"]
    )


def test_portable_legacy_record_reuses_safe_six_and_skips_only_leaking_four(tmp_path, monkeypatch):
    original = legacy_application(tmp_path, monkeypatch)
    root = tmp_path / "clone"
    shutil.copytree(original.root, root, ignore=shutil.ignore_patterns(".anaxigraph-v2"))
    clone = Application(root)
    imported = clone.scan()["portable_import"]
    assert imported["annotations_imported"] == 6
    assert imported["skipped_count"] == 4
    assert {item["id"] for item in imported["skipped"]} == {f"module:{path}" for path in LEAKS}
    assert all("source identity" in item["reason"] for item in imported["skipped"])
    assert clone.status()["annotations"] == {"current": 6, "stale": 0, "missing": 4}


def test_repairing_four_briefs_reuses_six_unchanged_xhigh_pattern_reviews(tmp_path, monkeypatch):
    app = legacy_application(tmp_path, monkeypatch, patterns=True)
    calls = []

    def provider(request, *_args, **_kwargs):
        calls.append((request["kind"], request["packet"]["modules"][0]["path"]))
        return result_for(request) if request["kind"] == "annotate" else answer(request)

    result = WorkflowRunner(app, provider).start("patterns", CONFIG, background=False)
    assert result["status"] == "completed", result
    assert result["reused_batches"] == 6
    assert result["reconciliation"]["evaluated_pairs"] == 1330
    assert {path for kind, path in calls if kind == "annotate"} == set(LEAKS)
    assert {path for kind, path in calls if kind == "patterns"} == set(LEAKS)
    assert len(calls) == 8
    assert not any(node["data"].get("annotation_stale_reason") for node in app.modules()["items"])

    # Git-only identity changes and imports must agree with the repaired cache,
    # including the four repaired reviews, rather than merely the six reused ones.
    for arguments in [
        ["init", "-q"],
        ["config", "user.name", "Fixture"],
        ["config", "user.email", "fixture@example.test"],
        ["add", "."],
        ["commit", "-qm", "Save repaired understanding"],
    ]:
        subprocess.run(["git", "-C", str(app.root), *arguments], check=True, capture_output=True)
    app.scan()
    assert all(row["status"] == "current" for row in app.reviews("patterns")["items"])
    app.export_record()
    root = tmp_path / "repaired-clone"
    shutil.copytree(app.root, root, ignore=shutil.ignore_patterns(".git", ".anaxigraph-v2"))
    cloned = Application(root)
    imported = cloned.scan()["portable_import"]
    assert imported["annotations_imported"] == 10
    assert imported["analyses_imported"] == 10
    repeated = WorkflowRunner(cloned, provider).start("patterns", CONFIG, background=False)
    assert repeated["status"] == "completed", repeated
    assert repeated["reused_batches"] == 10
    assert len(calls) == 8


@pytest.mark.parametrize("filename", ["record", "server"])
def test_bare_word_input_files_do_not_turn_ordinary_english_into_source_leaks(tmp_path, filename):
    app = application(tmp_path)
    (app.root / filename).write_text("A repository note without a code-shaped path.\n")
    app.scan()
    request = app.analysis_request("annotate", ["part0.py"])
    result = result_for(request)
    result["annotations"][0]["contracts"][0]["statement"] = (
        "Store a record on the server and report status for each module."
    )
    assert app.submit_analysis(request["request_id"], result)["saved"]


def test_matchers_reuse_annotation_only_graph_copies_without_retaining_graphs(tmp_path):
    app = application(tmp_path)
    graph = app._current()
    behavior._MATCHERS.clear()
    behavior._compile_matcher.cache_clear()
    matcher = behavior.behavior_matcher(graph)
    for _ in range(100):
        assert behavior.behavior_matcher(graph) is matcher
    copy = replace(
        graph,
        nodes=tuple(replace(node, data=node.data | {"annotation": {}}) for node in graph.nodes),
    )
    assert behavior.behavior_matcher(copy) is matcher
    assert behavior._compile_matcher.cache_info().misses == 1

    renamed = replace(
        copy,
        nodes=tuple(
            replace(node, name="AlternativeResort") if node.name == "DemoResort" else node
            for node in copy.nodes
        ),
    )
    changed = behavior.behavior_matcher(renamed)
    assert changed is not matcher
    assert changed("Use AlternativeResort.")
    assert not changed("Use DemoResort.")
    reference = weakref.ref(copy)
    del copy
    gc.collect()
    assert reference() is None
    for i in range(10):
        behavior.behavior_matcher(replace(graph, diagnostics=(str(i),)))
    assert len(behavior._MATCHERS) <= 4
    assert behavior._compile_matcher.cache_info().currsize <= 4
