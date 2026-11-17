"""New work captures current files; saved result submission still honors its original job."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_pattern_decisions import make_application
from test_review_applicability import saved_review
from test_workflows import answer

from anaxigraph.application import Application
from anaxigraph.interfaces.http import create_app
from anaxigraph.interfaces.mcp import create_mcp
from anaxigraph.workflows.engine import WorkflowRunner


def request_through(app, transport, kind, scope):
    if transport == "python":
        return app.analysis_request(kind, scope)
    if transport == "cli":
        result = subprocess.run(
            [
                str(Path(sys.executable).parent / "anaxigraph"),
                "request",
                kind,
                "--repo",
                str(app.root),
                "--scope",
                *scope,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    if transport == "http":
        with TestClient(
            create_app(app), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)
        ) as client:
            result = client.post("/api/analysis/request", json={"kind": kind, "scope": scope})
            assert result.status_code == 200, result.text
            return result.json()

    async def call():
        result = await create_mcp(app).call_tool("analysis_request", {"kind": kind, "scope": scope})
        return json.loads(result[0].text)

    return asyncio.run(call())


@pytest.mark.parametrize("transport", ["python", "cli", "http", "mcp"])
@pytest.mark.parametrize("kind", ["annotate", "fresh-eyes"])
def test_new_request_refreshes_the_checkout_without_an_explicit_scan(tmp_path, transport, kind):
    app = make_application(tmp_path)
    old = app.prepare_change("Improve delivery")
    source = tmp_path / "delivery.py"
    source.write_text("def deliver(items):\n    return sorted(items)\n")
    scope = ["delivery.py"] if kind == "annotate" else ["unrelated.py"]
    request = request_through(app, transport, kind, scope)
    checked = Application(tmp_path)._current()
    stored = app.store.get_review(str(tmp_path), request["request_id"], "request")["result"]
    assert checked.snapshot.id == stored["snapshot_id"] != old["snapshot_id"]
    assert app.store.stats(str(tmp_path))["pinned_snapshot_id"] == old["baseline_id"]
    if kind == "annotate":
        assert request["packet"]["source"]["delivery.py"] == source.read_text()
    else:
        packet = json.dumps(request["packet"])
        assert "source" not in request["packet"]
        assert "delivery.py" not in packet and "unrelated.py" not in packet


def test_first_manual_annotation_request_initializes_the_structural_map(tmp_path):
    (tmp_path / "service.py").write_text("def run():\n    return True\n")
    app = Application(tmp_path)
    request = app.analysis_request("annotate", ["service.py"])
    assert request["packet"]["source"]["service.py"]
    assert app.status()["modules"] == 1


@pytest.mark.parametrize("kind", ["pattern-screen", "patterns", "architecture", "delivery"])
def test_other_manual_review_kinds_also_capture_current_inputs(tmp_path, kind):
    app = make_application(tmp_path)
    old = app._current().snapshot.id
    (tmp_path / "delivery.py").write_text("def deliver(items):\n    return sorted(items)\n")
    request = app.analysis_request(kind, None if kind == "delivery" else ["unrelated.py"])
    assert request["snapshot_id"] == app._current().snapshot.id != old


@pytest.mark.parametrize("kind", ["pattern-screen", "architecture"])
def test_refresh_does_not_make_changed_module_briefs_current(tmp_path, kind):
    app = make_application(tmp_path)
    (tmp_path / "delivery.py").write_text("def deliver(items):\n    return sorted(items)\n")
    with pytest.raises(ValueError, match="(?i)(annotate|current)"):
        app.analysis_request(kind, ["delivery.py"])
    assert app.inspect("module:delivery.py")["entity"]["data"]["annotation_status"] == "stale"


@pytest.mark.parametrize("scan_first", [False, True])
def test_product_request_keeps_durable_child_identity_across_unrelated_edits(tmp_path, scan_first):
    app = make_application(tmp_path)
    child = saved_review(app, "fresh-eyes")
    original = app.store.get_review(str(tmp_path), child["request_id"], "fresh-eyes")
    (tmp_path / "unrelated.py").write_text("def lookup(value):\n    return str(value)\n")
    if scan_first:
        app.scan()
    product = app.analysis_product_request([child["request_id"]])
    stored = app.store.get_review(str(tmp_path), product["request_id"], "request")["result"]
    assert stored["children"][0]["request_id"] == child["request_id"]
    app.submit_analysis(product["request_id"], answer(product))
    (tmp_path / "README.md").write_text("Another unrelated edit.\n")
    saved = app.inspect("review:" + product["request_id"])["entity"]["data"]
    assert saved["status"] == "current"
    assert app.analysis_saved(product["request_id"])
    retained = app.store.get_review(str(tmp_path), child["request_id"], "fresh-eyes")
    assert retained["result"] == original["result"]
    assert retained["snapshot_id"] == original["snapshot_id"]
    assert retained["created_at"] == original["created_at"]


def test_old_annotation_submission_is_rejected_after_a_fresh_request(tmp_path):
    app = make_application(tmp_path)
    old = app.analysis_request("annotate", ["delivery.py"])
    result = answer(old)
    (tmp_path / "delivery.py").write_text("def deliver(items):\n    return sorted(items)\n")
    current = app.analysis_request("annotate", ["delivery.py"])
    assert current["request_id"] != old["request_id"]
    with pytest.raises(ValueError, match="stale snapshot"):
        app.submit_analysis(old["request_id"], result)


@pytest.mark.parametrize("phase", ["request", "before_batch", "reconciliation"])
def test_workflow_cannot_reuse_its_way_past_a_checkout_change(tmp_path, monkeypatch, phase):
    app = make_application(tmp_path)
    calls = []

    def provider(request, *_args, **_kwargs):
        calls.append(request["kind"])
        return answer(request)

    config = {"workers": 1, "stages": {"architecture": {"model": "synthetic-request-fixture"}}}
    runner = WorkflowRunner(app, provider)
    first = runner.start("architecture", config, ["delivery.py"], background=False)
    assert first["status"] == "completed", first
    calls.clear()
    edited = False

    def edit_once():
        nonlocal edited
        if not edited:
            edited = True
            (tmp_path / "unrelated.py").write_text("def lookup(value):\n    return str(value)\n")

    if phase == "request":
        original = app.analysis_request

        def intervening_edit(*args, **kwargs):
            edit_once()
            return original(*args, **kwargs)

        monkeypatch.setattr(app, "analysis_request", intervening_edit)
    elif phase == "before_batch":
        original = runner._batch

        def intervening_edit(*args, **kwargs):
            edit_once()
            app.scan()
            return original(*args, **kwargs)

        monkeypatch.setattr(runner, "_batch", intervening_edit)
    else:
        original = app.analysis_reconcile

        def intervening_edit(*args, **kwargs):
            result = original(*args, **kwargs)
            edit_once()
            app.scan()
            return result

        monkeypatch.setattr(app, "analysis_reconcile", intervening_edit)
    second = runner.start("architecture", config, ["delivery.py"], background=False)
    assert second["status"] == "failed", second
    assert calls == []
    if phase == "reconciliation":
        assert "Checkout changed" in second["error"]
    else:
        assert any(
            item["phase"] == "request" and item["error_kind"] == "ValueError"
            for item in second["attempt_failures"]
        )
