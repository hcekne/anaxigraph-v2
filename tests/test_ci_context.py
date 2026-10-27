"""Design-review identity follows visible topology, never hidden interface hashes."""

from __future__ import annotations

import hashlib
import json

from test_workflows import answer

from anaxigraph.analysis.ci_context import component_context
from anaxigraph.application import Application
from anaxigraph.indexing import index
from anaxigraph.workflows.engine import WorkflowRunner


def write(root, path, source):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)


def test_src_package_components_are_distinct_and_js_areas_are_inventory_only(tmp_path):
    write(tmp_path, "src/pkg/storage/store.py", "def save():\n    return 1\n")
    write(
        tmp_path,
        "src/pkg/analysis/read.py",
        "from pkg.storage.store import save\ndef read():\n    return save()\n",
    )
    write(tmp_path, "src/pkg/api.py", "def start():\n    return 1\n")
    write(tmp_path, "web/src/components/Button.tsx", "export const Button = () => null;\n")
    write(tmp_path, "web/src/services/client.ts", "export const load = () => 1;\n")
    context = component_context(index(tmp_path))
    areas = {item["root"]: item for item in context["areas"]}
    assert {"src/pkg/analysis", "src/pkg/storage", "src/pkg"} <= areas.keys()
    assert areas["src/pkg/analysis"]["modules"] == 1
    assert areas["web/src/components"]["modules"] == 0
    assert areas["web/src/components"]["unparsed_source_count"] == 1
    assert context["unparsed_source_count"] == 2
    assert ("src/pkg/analysis", "src/pkg/storage") in context["area_dependencies"]
    assert not any("web/" in area for link in context["area_dependencies"] for area in link)
    assert "inventory only" in context["limitations"]
    assert "do not establish interface compatibility" in context["limitations"]


def test_identity_is_exactly_the_visible_context_and_ignores_internal_signatures(tmp_path):
    write(
        tmp_path, "src/pkg/payments/charge.py", "def charge(value: int) -> int:\n    return value\n"
    )
    first_graph = index(tmp_path)
    first = component_context(first_graph)
    visible = {key: value for key, value in first.items() if key != "identity"}
    assert (
        first["identity"]
        == hashlib.sha256(json.dumps(visible, sort_keys=True).encode()).hexdigest()
    )
    write(
        tmp_path,
        "src/pkg/payments/charge.py",
        "def charge(value: float, *, rounds: int = 2) -> float:\n    return round(value, rounds)\n",
    )
    second_graph = index(tmp_path)
    assert first_graph.snapshot.id != second_graph.snapshot.id
    assert component_context(second_graph) == first


def test_visible_component_moves_and_cross_area_dependencies_invalidate_identity(tmp_path):
    write(tmp_path, "src/pkg/payments/charge.py", "def charge():\n    return 1\n")
    write(tmp_path, "src/pkg/kitchen/fan.py", "def speed():\n    return 1\n")
    first = component_context(index(tmp_path))
    write(
        tmp_path,
        "src/pkg/payments/charge.py",
        "from pkg.kitchen.fan import speed\ndef charge():\n    return speed()\n",
    )
    linked = component_context(index(tmp_path))
    assert linked["identity"] != first["identity"]
    assert ("src/pkg/payments", "src/pkg/kitchen") in linked["area_dependencies"]
    source = tmp_path / "src/pkg/kitchen/fan.py"
    write(tmp_path, "src/pkg/ventilation/fan.py", source.read_text())
    source.unlink()
    moved = component_context(index(tmp_path))
    assert moved["identity"] != linked["identity"]
    assert any(area["root"] == "src/pkg/ventilation" for area in moved["areas"])


def test_ci_review_reuses_signature_edit_but_new_component_requires_a_new_review(tmp_path):
    write(
        tmp_path, "src/pkg/payments/charge.py", "def charge(value: int) -> int:\n    return value\n"
    )
    write(
        tmp_path,
        ".github/workflows/checks.yml",
        "jobs:\n  test:\n    steps:\n      - run: pytest\n",
    )
    write(
        tmp_path,
        "anaxigraph/ci-policy.json",
        json.dumps(
            {
                "version": 1,
                "targets": [
                    {
                        "id": "payments",
                        "command": "pytest",
                        "inputs": ["src/pkg/payments/**"],
                        "cache": True,
                    }
                ],
            }
        ),
    )
    app = Application(tmp_path)
    calls = []

    def provider(request, *_args, **_kwargs):
        calls.append(request["kind"])
        return answer(request)

    runner = WorkflowRunner(app, provider)
    first = runner.start("ci", background=False)
    assert first["status"] == "completed", first
    options = {
        "changed_paths": ["src/pkg/payments/charge.py"],
        "runtime_context": {"python": "3.12"},
    }
    before = app.ci_plan(**options)["selected_targets"][0]["cache_key"]
    write(
        tmp_path,
        "src/pkg/payments/charge.py",
        "def charge(value: int, *, fee: int = 1) -> int:\n    return value + fee\n",
    )
    app.scan()
    second = runner.start("ci", background=False)
    assert second["status"] == "completed" and second["reused_batches"] == 1
    assert calls == ["delivery"]
    after = app.ci_plan(**options)["selected_targets"][0]["cache_key"]
    assert before is not None and after is not None and before != after
    write(tmp_path, "src/pkg/kitchen/fan.py", "def speed():\n    return 1\n")
    app.scan()
    third = runner.start("ci", background=False)
    assert third["status"] == "completed" and third["reused_batches"] == 0
    assert calls == ["delivery", "delivery"]
