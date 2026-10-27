"""Saved reasoning choices survive validation, repeat runs and portable imports."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy

import pytest
from test_workflows import answer, app

from anaxigraph.application import Application
from anaxigraph.workflows.engine import WorkflowRunner


def configuration():
    worker = {
        "provider": "codex",
        "model": "fixture",
        "reasoning_effort": "xhigh",
        "fallbacks": [{"provider": "codex", "model": "backup", "reasoning_effort": "high"}],
    }
    reviewer = {"provider": "codex", "model": "reviewer", "reasoning_effort": "medium"}
    return {
        "pattern_mode": "exhaustive",
        "timeout_seconds": None,
        "stages": {
            kind: deepcopy(worker)
            for kind in ("annotate", "patterns", "architecture", "fresh-eyes")
        },
        "review_passes": [reviewer],
    }


def clone(original, tmp_path):
    root = tmp_path / "clone"
    root.mkdir()
    for source in original.root.glob("unit*.py"):
        shutil.copy2(source, root / source.name)
    shutil.copytree(original.root / "anaxigraph", root / "anaxigraph")
    return Application(root)


@pytest.mark.parametrize("kind", ["patterns", "fresh-eyes"])
def test_identical_reasoning_pipeline_reuses_saved_and_portable_results(tmp_path, kind):
    root = tmp_path / "original"
    root.mkdir()
    original = app(root, count=2)
    calls = []

    def provider(request, worker, **_kwargs):
        calls.append((request["kind"], worker["reasoning_effort"]))
        return answer(request)

    config = configuration()
    runner = WorkflowRunner(original, provider)
    first = runner.start(kind, config, background=False)
    assert first["status"] == "completed", first
    count = len(calls)
    repeated = runner.start(kind, config, background=False)
    assert repeated["status"] == "completed", repeated
    assert repeated["reused_batches"] == repeated["total_batches"]
    assert len(calls) == count

    record = json.loads((root / "anaxigraph/record.json").read_text())
    executions = [module["annotation"]["execution"] for module in record["modules"]]
    executions.extend(row["body"]["result"]["execution"] for row in record["analyses"])
    assert len(executions) >= 3
    for execution in executions:
        assert execution["worker"]["reasoning_effort"] == "xhigh"
        assert execution["pipeline"]["worker"]["reasoning_effort"] == "xhigh"
        assert execution["pipeline"]["worker"]["fallbacks"][0]["reasoning_effort"] == "high"
        assert execution["pipeline"]["review_passes"][0]["reasoning_effort"] == "medium"
        assert execution["review_passes"][0]["reasoning_effort"] == "medium"

    imported = clone(original, tmp_path)
    scanned = imported.scan()
    assert scanned["portable_import"]["annotations_imported"] == 2
    assert scanned["portable_import"]["analyses_imported"] >= 1
    cloned = WorkflowRunner(imported, provider).start(kind, config, background=False)
    assert cloned["status"] == "completed", cloned
    assert cloned["reused_batches"] == cloned["total_batches"]
    assert len(calls) == count


@pytest.mark.parametrize("provider,effort", [("claude", "xhigh"), ("codex", "unlimited")])
@pytest.mark.parametrize(
    "location", ["worker", "pipeline", "fallback", "review", "pipeline_review"]
)
def test_invalid_reasoning_is_rejected_in_every_execution_worker(
    tmp_path, provider, effort, location
):
    application = app(tmp_path, count=1)
    application.scan()
    request = application.analysis_request("annotate")
    result = answer(request)
    config = configuration()
    selected = config["stages"]["annotate"]
    execution = {
        "pipeline": {
            "worker": deepcopy(selected),
            "review_passes": deepcopy(config["review_passes"]),
        },
        "worker": {key: value for key, value in selected.items() if key != "fallbacks"},
        "review_passes": deepcopy(config["review_passes"]),
    }
    target = {
        "worker": execution["worker"],
        "pipeline": execution["pipeline"]["worker"],
        "fallback": execution["pipeline"]["worker"]["fallbacks"][0],
        "review": execution["review_passes"][0],
        "pipeline_review": execution["pipeline"]["review_passes"][0],
    }[location]
    target.update(provider=provider, reasoning_effort=effort)
    result["execution"] = execution
    with pytest.raises(ValueError, match="reasoning_effort"):
        application.submit_analysis(request["request_id"], result)
    assert application.status()["annotations"]["current"] == 0


@pytest.mark.parametrize("location", ["annotation", "review"])
def test_portable_import_does_not_accept_invalid_reasoning(tmp_path, location):
    root = tmp_path / "original"
    root.mkdir()
    original = app(root, count=1)
    runner = WorkflowRunner(original, lambda request, *_args, **_kwargs: answer(request))
    assert runner.start("patterns", configuration(), background=False)["status"] == "completed"
    record_path = root / "anaxigraph/record.json"
    record = json.loads(record_path.read_text())
    execution = (
        record["modules"][0]["annotation"]["execution"]
        if location == "annotation"
        else next(row for row in record["analyses"] if row["kind"] == "patterns")["body"]["result"][
            "execution"
        ]
    )
    execution["worker"].update(provider="claude", reasoning_effort="xhigh")
    record_path.write_text(json.dumps(record))
    target = clone(original, tmp_path)
    scanned = target.scan()
    imported = scanned["portable_import"]
    if location == "annotation":
        assert imported["compatible"] is False
        assert "reasoning_effort" in imported["issue"]
        assert target.status()["annotations"]["current"] == 0
    else:
        assert imported["analyses_imported"] == len(record["analyses"]) - 1
        assert "reasoning_effort" in imported["skipped"][0]["reason"]
        assert target.reviews("patterns")["total"] == 0
        assert target.reviews("architecture")["total"] == 3
