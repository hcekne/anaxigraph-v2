"""An unrelated checkout edit must not buy the same scoped analysis again."""

from __future__ import annotations

from collections import Counter

from test_workflows import answer

from anaxigraph.application import Application
from anaxigraph.workflows.engine import WorkflowRunner


def test_scoped_screen_and_detail_reuse_does_not_need_a_portable_record(tmp_path):
    for name in ("first", "second"):
        (tmp_path / f"{name}.py").write_text(f"def {name}(value):\n    return value\n")
    application = Application(tmp_path)
    calls = Counter()
    scout_scopes = []

    def provider(request, *_args, **_kwargs):
        modules = request["packet"].get("modules", [])
        calls[(request["kind"], modules[0]["path"] if modules else "")] += 1
        if request["kind"] == "architecture":
            scout_scopes.append(tuple(request["scope"]))
        if request["kind"] == "pattern-screen":
            return {
                "producer": "synthetic-scoped-reuse",
                "selections": [
                    {
                        "module_id": node["id"],
                        "pattern_ids": [request["packet"]["catalogue"][0]["id"]],
                    }
                    for node in request["packet"]["modules"]
                ],
            }
        return answer(request)

    runner = WorkflowRunner(application, provider)
    first = runner.start("patterns", background=False)
    assert first["status"] == "completed", first
    kinds = Counter()
    for (kind, _path), count in calls.items():
        kinds[kind] += count
    assert kinds == {
        "annotate": 2,
        "pattern-screen": 2,
        "patterns": 2,
        "architecture": 3,
        "delivery": 1,
    }
    calls.clear()
    unchanged = runner.start("patterns", background=False)
    assert unchanged["status"] == "completed", unchanged
    assert not calls
    scout_scopes.clear()

    # Force local reuse rather than recovering old reviews from a portable export.
    (tmp_path / "anaxigraph" / "record.json").unlink()
    (tmp_path / "first.py").write_text("def first(value):\n    return value + 1\n")
    changed = runner.start("patterns", background=False)
    assert changed["status"] == "completed", changed
    assert calls == Counter(
        {
            ("annotate", "first.py"): 1,
            ("pattern-screen", "first.py"): 1,
            ("patterns", "first.py"): 1,
            ("architecture", "first.py"): 3,
        }
    )
    assert changed["reconciliation"]["screened_pairs"] == 266
    assert changed["reconciliation"]["detailed_pairs"] == 2
    # A leaf edit leaves the other file's screen/detail cached, while the three
    # architectural scouts correctly revisit their bounded two-file context.
    assert scout_scopes == [("module:first.py", "module:second.py")] * 3


def test_opaque_resource_edit_prevents_reuse_of_identical_scoped_packets(tmp_path):
    (tmp_path / "unit.py").write_text("def operation(value):\n    return value\n")
    resource = tmp_path / "settings.json"
    resource.write_text('{"version":1}\n')
    application = Application(tmp_path)
    calls = Counter()

    def provider(request, *_args, **_kwargs):
        calls[request["kind"]] += 1
        if request["kind"] == "pattern-screen":
            return {
                "producer": "synthetic-resource-guard",
                "selections": [
                    {"module_id": node["id"], "pattern_ids": []}
                    for node in request["packet"]["modules"]
                ],
            }
        return answer(request)

    runner = WorkflowRunner(application, provider)
    initial = runner.start("patterns", background=False)
    assert initial["status"] == "completed", initial
    calls.clear()
    (tmp_path / "anaxigraph" / "record.json").unlink()
    resource.write_text('{"version":2}\n')
    refreshed = runner.start("patterns", background=False)
    assert refreshed["status"] == "completed", refreshed
    assert calls["pattern-screen"] == 1
    assert calls["patterns"] == 0
    assert calls["architecture"] == 3
