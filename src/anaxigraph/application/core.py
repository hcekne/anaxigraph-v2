"""One application facade coordinating graph, change, review and record operations."""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from anaxigraph import __version__
from anaxigraph.analysis.changes import change_review
from anaxigraph.analysis.guidance import module_for, neighborhood, prepare
from anaxigraph.analysis.portable import ANNOTATION_CONTRACT_VERSION
from anaxigraph.application import records, reviews
from anaxigraph.application.context import _bounds, context_fingerprints
from anaxigraph.contracts import Graph
from anaxigraph.indexing import IndexSession
from anaxigraph.storage import Store
from anaxigraph.workflows.engine import WorkflowRunner


class Application:
    """Compose independent components; do not duplicate reasoning in transports."""

    def __init__(self, root: Path, database: Path | None = None) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Repository root must be a directory")
        self.store = Store(database or self.root / ".anaxigraph-v2" / "knowledge.sqlite")
        self.indexer = IndexSession(self.root)
        self._graph_cache: tuple[tuple[int, ...], Graph] | None = None

    def _current(self) -> Graph:
        token = self.store.knowledge_token(str(self.root))
        if self._graph_cache is not None and self._graph_cache[0] == token:
            return self._graph_cache[1]
        graph = self.store.load_graph(str(self.root))
        if graph is None:
            raise ValueError("No map for this checkout; run scan first")
        contexts = context_fingerprints(graph)
        nodes = []
        for node in graph.nodes:
            data = dict(node.data)
            annotation = data.get("annotation")
            if annotation and (
                annotation.get("context_fingerprint") != contexts.get(node.path)
                or annotation.get("annotation_contract") != ANNOTATION_CONTRACT_VERSION
            ):
                data["annotation_status"] = "stale"
            nodes.append(replace(node, data=data))
        current = replace(graph, nodes=tuple(nodes))
        if self.store.knowledge_token(str(self.root)) == token:
            self._graph_cache = (token, current)
        return current

    def _require_live(self, graph: Graph) -> None:
        # Never accept an old job merely because an edit has not been scanned yet.
        if self.indexer.snapshot().id != graph.snapshot.id:
            raise ValueError(
                "Checkout changed after mapping; scan again before saving or validating"
            )

    def scan(self) -> dict[str, Any]:
        started = time.monotonic()
        graph = self.indexer.scan()
        # Avoid publishing a captured graph after a concurrent edit.
        self._require_live(graph)
        if (
            not self.indexer.unchanged
            or self.store.stats(str(self.root))["snapshot_id"] != graph.snapshot.id
        ):
            self.store.save_graph(graph)
        imported = self.import_record()
        exported = self.export_record()
        return {
            **self.status(),
            "elapsed_seconds": round(time.monotonic() - started, 4),
            "mapped_locally": True,
            "record_exported": exported["record_exported"],
            "portable_import": imported,
            "portable_bytes": exported["bytes"],
            "committed_pushed": False,
            "indexing": self.indexer.last_scan,
        }

    def status(self) -> dict[str, Any]:
        stats = self.store.stats(str(self.root))
        storage = {key: stats[key] for key in ("bytes", "max_bytes", "snapshots", "reviews")}
        if stats["snapshot_id"] is None:
            return {
                "version": __version__,
                "root": str(self.root),
                "snapshot": None,
                "modules": 0,
                "symbols": 0,
                "relationships": 0,
                "annotations": {"current": 0, "stale": 0, "missing": 0},
                "storage": storage,
                "portable": self.portable_status(),
            }
        graph = self._current()
        modules = [n for n in graph.nodes if n.kind == "module"]
        coverage = Counter(n.data.get("annotation_status", "missing") for n in modules)
        return {
            "version": __version__,
            "root": str(self.root),
            "snapshot": {
                key: value for key, value in asdict(graph.snapshot).items() if key != "files"
            }
            | {"file_count": len(graph.snapshot.files)},
            "modules": len(modules),
            "symbols": sum(n.kind in {"function", "class", "interface"} for n in graph.nodes),
            "relationships": len(graph.edges),
            "unresolved_relationships": sum(e.resolution != "resolved" for e in graph.edges),
            "annotations": {key: coverage[key] for key in ("current", "stale", "missing")},
            "diagnostics": list(graph.diagnostics)[:30],
            "storage": storage,
            "baseline_id": stats.get("pinned_snapshot_id"),
            "portable": self.portable_status(),
            "coverage": "Python/PYI syntax and scoped references; dynamic targets may be unresolved",
        }

    def modules(
        self, sort: str = "path", descending: bool = False, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        _bounds(limit)
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        if sort not in {"path", "loc", "functions", "function_count"}:
            raise ValueError("sort must be path, loc or functions")
        nodes = [node for node in self._current().nodes if node.kind == "module"]
        key = "function_count" if sort == "functions" else sort
        nodes.sort(
            key=lambda n: n.path if key == "path" else n.data.get(key, 0), reverse=descending
        )
        items = []
        for node in nodes[offset : offset + limit]:
            data = {
                key: node.data[key]
                for key in ("loc", "function_count", "parse_status", "is_test", "annotation_status")
                if key in node.data
            }
            annotation = node.data.get("annotation")
            if annotation:
                data["annotation"] = {"overview": annotation["overview"]}
            item = asdict(replace(node, data=data))
            if items and len(json.dumps(items + [item]).encode()) > 7600:
                break
            items.append(item)
        next_offset = offset + len(items)
        return {
            "items": items,
            "total": len(nodes),
            "offset": offset,
            "limit": limit,
            "next_offset": next_offset if next_offset < len(nodes) else None,
        }

    def inspect(self, entity_id: str, limit: int = 30) -> dict[str, Any]:
        graph = self._current()
        if entity_id.startswith("review:"):
            row = next(
                (
                    row
                    for row in self.store.reviews(str(self.root), include_requests=False)
                    if row["kind"] != "request" and row["id"] == entity_id[7:]
                ),
                None,
            )
            if row is None:
                raise ValueError("Saved review not found")
            row = self._review_state(row, graph)
            return {
                "entity": {
                    "id": entity_id,
                    "kind": "review",
                    "name": row["kind"],
                    "path": "",
                    "line": 1,
                    "data": row,
                },
                "incoming": [],
                "outgoing": [],
                "neighbors": [],
                "snapshot_id": graph.snapshot.id,
            }
        result = neighborhood(graph, entity_id, _bounds(limit))
        result["snapshot_id"] = graph.snapshot.id
        entity = result["entity"]
        if entity["kind"] == "function":
            owner = module_for(graph, entity["path"])
            if owner:
                annotation = owner.data.get("annotation", {})
                entity["data"]["overview"] = next(
                    (
                        item["overview"]
                        for item in annotation.get("functions", [])
                        if item["node_id"] == entity["id"]
                    ),
                    None,
                )
                entity["data"]["annotation_status"] = owner.data.get("annotation_status", "missing")
        return result

    def prepare_change(
        self, task: str, limit: int = 12, baseline_id: str | None = None
    ) -> dict[str, Any]:
        if not isinstance(task, str) or not task.strip() or len(task) > 4000:
            raise ValueError("task must be nonempty text of at most 4,000 characters")
        graph = self._current()
        self._require_live(graph)
        matches = self.store.search(str(self.root), task, _bounds(limit, 30))
        statuses = {
            n.path: n.data.get("annotation_status", "missing")
            for n in graph.nodes
            if n.kind == "module"
        }
        for match in matches:
            match["annotation_status"] = statuses.get(match["path"], "missing")
        pinned = self.store.pin_baseline(str(self.root), baseline_id)
        return prepare(graph, matches, task) | {
            "baseline_id": pinned,
            "baseline_basis": "pinned-change-baseline",
            "baseline_note": "Preserved across scans. Supply a baseline_id to begin a new comparison.",
        }

    def assess_plan(self, task: str, files: list[str]) -> dict[str, Any]:
        if not isinstance(files, list) or not files or len(files) > 30:
            raise ValueError("files must name between 1 and 30 relative paths")
        if any(
            not isinstance(p, str) or Path(p).is_absolute() or ".." in Path(p).parts for p in files
        ):
            raise ValueError("Plan files must be repository-relative paths")
        brief = self.prepare_change(task)
        existing = {node["path"] for node in brief["likely_owners"]}
        findings = []
        for path in files:
            if path not in self._current().snapshot.files and existing:
                findings.append(
                    {
                        "path": path,
                        "kind": "reuse-opportunity",
                        "message": "Compare existing responsibility owners before adding a sibling module.",
                        "existing": sorted(existing),
                    }
                )
        return {
            "snapshot_id": brief["snapshot_id"],
            "files": files,
            "findings": findings,
            "brief": brief,
            "status": "advisory",
            "scope": "Placement and lexical reuse evidence; behavioral plan correctness is unresolved.",
        }

    def validate_change(
        self, receipts: list[dict[str, Any]] | None = None, baseline_id: str | None = None
    ) -> dict[str, Any]:
        graph = self._current()
        if baseline_id is not None and (not isinstance(baseline_id, str) or not baseline_id):
            raise ValueError("Baseline ID must be a nonempty snapshot identity")
        self._require_live(graph)
        stats = self.store.stats(str(self.root))
        selected = (
            baseline_id or stats.get("pinned_snapshot_id") or stats.get("previous_snapshot_id")
        )
        previous = self.store.load_graph(str(self.root), selected) if selected else None
        if selected and previous is None:
            raise ValueError("Baseline must identify a retained snapshot of this checkout")
        return change_review(graph, previous, receipts) | {
            "baseline_id": selected,
            "baseline_basis": "explicit"
            if baseline_id
            else ("pinned-change-baseline" if stats.get("pinned_snapshot_id") else "previous-scan"),
            "comparison": "explicit baseline"
            if baseline_id
            else (
                "pinned change baseline"
                if stats.get("pinned_snapshot_id")
                else ("previous scan" if selected else "no saved baseline")
            ),
        }

    def analysis_request(
        self, kind: str, scope: list[str] | None = None, **options: Any
    ) -> dict[str, Any]:
        return reviews.analysis_request(self, kind, scope, **options)

    def submit_analysis(self, request_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return reviews.submit_analysis(self, request_id, result)

    def reviews(self, kind: str | None = None, limit: int = 12, offset: int = 0) -> dict[str, Any]:
        return reviews.reviews(self, kind, limit, offset)

    def _review_state(self, row: dict[str, Any], graph: Graph) -> dict[str, Any]:
        return reviews._review_state(self, row, graph)

    def _rebuild_request(self, graph: Graph, kind: str, body: dict[str, Any]) -> dict[str, Any]:
        return reviews._rebuild_request(self, graph, kind, body)

    def analysis_product_request(self, review_ids: list[str]) -> dict[str, Any]:
        return reviews.analysis_product_request(self, review_ids)

    def analysis_saved(self, request_id: str, pipeline: dict[str, Any] | None = None) -> bool:
        return reviews.analysis_saved(self, request_id, pipeline)

    def analysis_reconcile(
        self, kind: str, review_ids: list[str], scope: list[str] | None = None
    ) -> dict[str, Any]:
        return reviews.analysis_reconcile(self, kind, review_ids, scope)

    def portable_status(self) -> dict[str, Any]:
        return records.portable_status(self)

    def _portable_token(self) -> tuple:
        return records._portable_token(self)

    def export_record(self) -> dict[str, Any]:
        return records.export_record(self)

    def import_record(self) -> dict[str, Any]:
        return records.import_record(self)

    @property
    def _workflows(self) -> WorkflowRunner:
        if not hasattr(self, "_workflow_runner"):
            self._workflow_runner = WorkflowRunner(self)
        return self._workflow_runner

    def workflow_start(
        self,
        kind: str,
        config: dict | None = None,
        scope: list[str] | None = None,
        wait: bool = False,
    ) -> dict[str, Any]:
        return self._workflows.start(kind, config, scope, background=not wait)

    def workflow_status(self, run_id: str | None = None) -> dict[str, Any]:
        return self._workflows.status(run_id)

    def workflow_resume(self, run_id: str, wait: bool = False) -> dict[str, Any]:
        return self._workflows.resume(run_id, background=not wait)

    def workflow_configure(self, config: dict | None = None) -> dict[str, Any]:
        return self._workflows.configuration(config)
