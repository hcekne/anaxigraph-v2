"""Declared package facts are bounded, source-pinned and stripped of URL credentials."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from anaxigraph.analysis.packets import build_request
from anaxigraph.analysis.portable import input_identity
from anaxigraph.contracts import Graph, Snapshot
from anaxigraph.indexing import index
from anaxigraph.indexing.package_metadata import MAX_MANIFEST_BYTES, capture_package_metadata


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_declared_python_and_node_metadata_are_compact_and_omit_commands_and_auth_urls(tmp_path):
    python = tmp_path / "pyproject.toml"
    python.write_text("""[project]
name = "pricing-kit"
version = "1.2.3"
dependencies = ["requests>=2", "private-lib @ https://alice:password@example.test/private.whl"]
[project.scripts]
pricing = "private.entry:run_secret_command"
[tool.uv.workspace]
members = ["packages/pricing", "packages/money"]
[tool.pytest.ini_options]
testpaths = ["tests/unit"]
""")
    node = tmp_path / "package.json"
    node.write_text(
        json.dumps(
            {
                "name": "@example/app",
                "version": "2.1.0",
                "dependencies": {
                    "@example/pricing": "workspace:*",
                    "private": "git+https://secret@example.test/repo",
                },
                "workspaces": {"packages": [f"packages/{i}" for i in range(22)]},
                "scripts": {"test": "echo never-send-this-command"},
            }
        )
    )
    facts = capture_package_metadata(
        tmp_path, {path.name: fingerprint(path) for path in (python, node)}
    )
    assert facts["status"] == "declared"
    py, js = facts["manifests"]
    assert (py["name"], py["version"]) == ("pricing-kit", "1.2.3")
    assert py["workspace_members"] == ["packages/pricing", "packages/money"]
    assert py["test_paths"] == ["tests/unit"]
    assert py["script_names"] == ["pricing"] and py["script_kind"] == "console-entry-point"
    assert js["script_names"] == ["test"] and len(js["workspace_members"]) == 20
    assert js["workspace_members_truncated"]
    assert py["dependency_constraints"] == [
        {"name": "requests", "constraint": ">=2", "kind": "runtime"},
        {"name": "private-lib", "constraint": "direct-reference-omitted", "kind": "runtime"},
    ]
    encoded = json.dumps(facts)
    for secret in (
        "password",
        "secret@",
        "example.test",
        "never-send-this-command",
        "run_secret_command",
    ):
        assert secret not in encoded
    assert all(
        item["evidence"] == [{"path": item["path"], "line": 1}] for item in facts["manifests"]
    )


def test_oversize_invalid_missing_changed_and_symlinked_manifests_stay_honest(tmp_path):
    assert capture_package_metadata(tmp_path, {})["status"] == "unknown"
    manifest = tmp_path / "package.json"
    manifest.write_text("x" * (MAX_MANIFEST_BYTES + 1))
    large = capture_package_metadata(tmp_path, {manifest.name: fingerprint(manifest)})
    assert large["status"] == "unknown" and "budget" in large["manifests"][0]["reason"]
    manifest.write_text("not JSON")
    invalid = capture_package_metadata(tmp_path, {manifest.name: fingerprint(manifest)})
    assert invalid["status"] == "unknown" and "parsed" in invalid["manifests"][0]["reason"]
    manifest.write_text('{"name":"before"}')
    previous = {manifest.name: fingerprint(manifest)}
    manifest.write_text('{"name":"after"}')
    with pytest.raises(ValueError, match="changed"):
        capture_package_metadata(tmp_path, previous)
    manifest.unlink()
    target = tmp_path / "private.json"
    target.write_text('{"name":"private"}')
    manifest.symlink_to(target)
    with pytest.raises(ValueError, match="captured"):
        capture_package_metadata(tmp_path, {manifest.name: fingerprint(target)})


def test_package_context_cannot_enter_a_blind_or_other_nonarchitecture_packet(tmp_path):
    graph = Graph(Snapshot("snapshot", str(tmp_path), None, False, {}, "now"), (), ())
    for kind in ("fresh-eyes", "patterns", "annotate"):
        with pytest.raises(ValueError, match="only to architecture"):
            build_request(graph, kind, package_context={"name": "must-not-leak"})


def test_declared_version_changes_review_inputs_without_entering_retention_options(tmp_path):
    (tmp_path / "pricing.py").write_text("def price():\n    return 1\n")
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('[project]\nname="pricing-kit"\nversion="1.0.0"\n')
    graph = index(tmp_path)
    graph = replace(
        graph,
        nodes=tuple(
            replace(
                node,
                data=node.data
                | {
                    "annotation_status": "current",
                    "annotation": {
                        "overview": "Calculates an amount.",
                        "capabilities": ["Calculates an amount."],
                    },
                },
            )
            if node.kind == "module"
            else node
            for node in graph.nodes
        ),
    )
    before = capture_package_metadata(tmp_path, graph.snapshot.files)
    initial = build_request(graph, "architecture", package_context=before)
    manifest.write_text('[project]\nname="pricing-kit"\nversion="2.0.0"\n')
    after = capture_package_metadata(tmp_path, {"pyproject.toml": fingerprint(manifest)})
    updated = build_request(graph, "architecture", package_context=after)
    assert input_identity(updated) != input_identity(initial)
    assert "package_context" not in updated["request_options"]
    assert updated["packet"]["package_context"]["manifests"][0]["version"] == "2.0.0"
