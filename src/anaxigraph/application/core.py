"""One application facade coordinating graph, change, review and record operations."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from anaxigraph import __version__
from anaxigraph.analysis import extension
from anaxigraph.analysis.behavior import annotation_behavior_problems
from anaxigraph.analysis.changes import change_review, changed_paths
from anaxigraph.analysis.charter import CHARTER, module_facts
from anaxigraph.analysis.guidance import module_for, neighborhood, prepare
from anaxigraph.analysis.history import cochange_advice
from anaxigraph.analysis.portable import ANNOTATION_CONTRACT_VERSION
from anaxigraph.analysis.retrieval import rank_owners
from anaxigraph.application import architecture as component_architecture
from anaxigraph.application import delivery, records
from anaxigraph.application import reviews as review_operations
from anaxigraph.application.context import (
    _bounds,
    context_fingerprints,
    dependency_implementation_fingerprints,
)
from anaxigraph.contracts import Graph, Node
from anaxigraph.evidence import Evidence
from anaxigraph.indexing import IndexSession
from anaxigraph.indexing.history import capture_history
from anaxigraph.indexing.python import is_test
from anaxigraph.storage import Store
from anaxigraph.workflows.engine import WorkflowRunner


def _extension_review(graph: Graph, previous: Graph | None) -> dict[str, Any]:
    before = previous.snapshot.files if previous else {}
    changed = set(changed_paths(graph, previous)) & set(graph.snapshot.files)
    added = {path for path in changed if path not in before}
    findings = extension.change_findings(graph, added, changed - added)
    production = {path for path in changed if not is_test(path)}
    return {
        "status": "concern" if findings else "no_closed_family_growth",
        "findings": findings,
        "change_check_reach": extension.check_reach(graph, production),
        "note": "Members added while editing a family's registrars; record why if the family "
        "stays closed. Import reach is an over-approximation, not a CI selection.",
    }


def _plan_findings(graph: Graph, brief: dict, files: list[str]) -> tuple[list, list]:
    """Closed-family decisions, then one reuse prompt for new modules outside any family."""
    added = {path for path in files if path not in graph.snapshot.files}
    closed = extension.change_findings(graph, added, set(files) - added)
    joining = {path for item in closed for path in item["adding"]}
    siblings = sorted(path for path in added - joining if not is_test(path))
    existing = sorted(node["path"] for node in brief["likely_owners"])
    if not siblings or not existing:
        return closed, []
    return closed, [
        {
            "paths": siblings,
            "kind": "reuse-opportunity",
            "message": "Compare existing responsibility owners before adding these modules.",
            "existing": existing,
        }
    ]


def _record_view(identity, kind, name, data, snapshot=None) -> dict:
    return {
        "entity": asdict(Node(identity, kind, name, "", data=data)),
        "incoming": [],
        "outgoing": [],
        "neighbors": [],
        "snapshot_id": snapshot,
    }


class Application:
    """Compose independent components; do not duplicate reasoning in transports."""

    def __init__(
        self, root: Path, database: Path | None = None, *, excluded: tuple[str, ...] = ()
    ) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Repository root must be a directory")
        self.store = Store(database or self.root / ".anaxigraph-v2" / "knowledge.sqlite")
        self.indexer = IndexSession(self.root, excluded=excluded)
        self.evidence = Evidence(
            self.root,
            enabled=os.environ.get("ANAXIGRAPH_EVIDENCE", "on") != "off",
            arm=os.environ.get("ANAXIGRAPH_EXPERIMENT_ARM", "unassigned"),
            experiment_id=os.environ.get("ANAXIGRAPH_EXPERIMENT_ID"),
        )
        self._graph_cache: tuple[tuple[int, ...], Graph] | None = None
        self._export_cache: tuple[tuple[Any, ...], dict[str, Any]] | None = None
        self._import_cache: tuple[Any, ...] | None = None

    def _current(self) -> Graph:
        token = self.store.knowledge_token(str(self.root))
        if self._graph_cache is not None and self._graph_cache[0] == token:
            return self._graph_cache[1]
        graph = self.store.load_graph(str(self.root))
        if graph is None:
            raise ValueError("No map for this checkout; run scan first")
        contexts = context_fingerprints(graph)
        implementations = dependency_implementation_fingerprints(graph)
        behavior_problems = annotation_behavior_problems(graph)
        nodes = []
        for node in graph.nodes:
            data = dict(node.data)
            annotation = data.get("annotation")
            if annotation and (
                annotation.get("context_fingerprint") != contexts.get(node.path)
                or annotation.get("annotation_contract") != ANNOTATION_CONTRACT_VERSION
            ):
                data["annotation_status"] = "stale"
            if node.id in behavior_problems:
                data["annotation_status"] = "stale"
                data["annotation_stale_reason"] = behavior_problems[node.id]
            if annotation and node.kind == "module":
                prior = annotation.get("dependency_implementation_fingerprint")
                data["dependency_implementation_changed"] = (
                    prior != implementations[node.path] if prior is not None else None
                )
                data["behavioral_validation"] = "not_independently_verified"
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

    def _history_advice(self, graph: Graph, paths: set[str], revision: str | None) -> dict:
        key = (revision, tuple(sorted(graph.snapshot.files)))
        cached = getattr(self, "_history_cache", None)
        if cached is None or cached[0] != key:
            cached = (key, capture_history(self.root, revision, set(graph.snapshot.files)))
            self._history_cache = cached
        return cochange_advice(cached[1], paths)

    def scan(self, publish: bool = False) -> dict[str, Any]:
        if type(publish) is not bool:
            raise ValueError("publish must be a boolean")
        started = time.monotonic()
        graph = self.indexer.scan()
        # Avoid publishing a captured graph after a concurrent edit.
        self._require_live(graph)
        if (
            not self.indexer.unchanged
            or self.store.stats(str(self.root))["snapshot_id"] != graph.snapshot.id
        ):
            self.store.save_graph(graph)
        try:
            imported = self.import_record()
        except ValueError as error:
            # An incompatible saved record must not prevent structural indexing.
            # Explicit publication still rejects it and preserves the original.
            if publish:
                raise
            self._require_live(graph)
            imported = {"record_found": True, "compatible": False, "issue": str(error)}
        exported = self.export_record() if publish else None
        status = self.status()
        portable = status["portable"]
        return {
            **status,
            "elapsed_seconds": round(time.monotonic() - started, 4),
            "mapped_locally": True,
            "record_exported": exported is not None,
            "portable_import": imported,
            "portable_bytes": portable["bytes"],
            "committed": portable["committed"],
            "pushed": portable["pushed"],
            "committed_pushed": portable["committed_pushed"],
            "indexing": self.indexer.last_scan,
        }

    def status(self) -> dict[str, Any]:
        stats = self.store.stats(str(self.root))
        graph = self._current() if stats["snapshot_id"] is not None else None
        nodes = graph.nodes if graph else ()
        edges = graph.edges if graph else ()
        modules = [node for node in nodes if node.kind == "module"]
        coverage = Counter(node.data.get("annotation_status", "missing") for node in modules)
        return {
            "version": __version__,
            "root": str(self.root),
            "snapshot": (
                {key: value for key, value in asdict(graph.snapshot).items() if key != "files"}
                | {"file_count": len(graph.snapshot.files)}
            )
            if graph
            else None,
            "modules": len(modules),
            "symbols": sum(node.kind in {"function", "class", "interface"} for node in nodes),
            "relationships": len(edges),
            "unresolved_relationships": sum(edge.resolution != "resolved" for edge in edges),
            "annotations": {key: coverage[key] for key in ("current", "stale", "missing")},
            "diagnostics": list(graph.diagnostics)[:30] if graph else [],
            "storage": {key: stats[key] for key in ("bytes", "max_bytes", "snapshots", "reviews")},
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
        graph = self._current()
        nodes = [node for node in graph.nodes if node.kind == "module"]
        key = "function_count" if sort == "functions" else sort
        nodes.sort(
            key=lambda n: n.path if key == "path" else n.data.get(key, 0), reverse=descending
        )
        items: list[dict[str, Any]] = []
        for node in nodes[offset : offset + limit]:
            facts = module_facts(graph, node)
            data = {
                key: node.data[key]
                for key in (
                    "loc",
                    "function_count",
                    "parse_status",
                    "is_test",
                    "annotation_status",
                    "annotation_stale_reason",
                    "dependency_implementation_changed",
                    "behavioral_validation",
                )
                if key in node.data
            }
            data["boundary"] = {
                "public_names": facts["public_surface"]["count"],
                "basis": facts["public_surface"]["basis"],
                "dependencies": facts["dependency_count"],
                "consumers": facts["consumer_count"],
                "private_accesses": facts["private_access_count"],
                "cycle_size": facts["cycle_size"],
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
        if entity_id == "charter":
            return _record_view(
                "charter", "charter", "Architecture principles", {"principles": CHARTER}
            )
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
            self.evidence.consulted(entity_id, graph.snapshot.id)
            return _record_view(entity_id, "review", row["kind"], row, graph.snapshot.id)

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
        self.evidence.consulted(entity_id, graph.snapshot.id)
        return result

    def prepare_change(
        self,
        task: str,
        limit: int = 12,
        baseline_id: str | None = None,
        continue_task: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(task, str) or not task.strip() or len(task) > 4000:
            raise ValueError("task must be nonempty text of at most 4,000 characters")
        graph = self._current()
        limit = _bounds(limit, 30)
        matches = rank_owners(graph, self.indexer.captured_sources(graph.snapshot), task, limit)
        task_key = hashlib.sha256(" ".join(task.casefold().split()).encode()).hexdigest()
        pinned = self.store.pin_baseline(
            str(self.root),
            baseline_id,
            task_key=task_key,
            revision=graph.snapshot.revision,
            continue_task=continue_task,
        )
        matched_paths = {match["path"] for match in matches}
        owner_ids = tuple(
            node.id for node in graph.nodes if node.kind == "module" and node.path in matched_paths
        )
        review_rows = [
            self._review_state(row, graph)
            for row in self.store.reviews(
                str(self.root), include_requests=False, module_ids=owner_ids, limit=6
            )
        ]
        review_rows = list(
            {
                row["id"]: row
                for row in review_rows
                + component_architecture.owner_reviews(self, graph, owner_ids)
            }.values()
        )
        brief = extension.design_brief(graph, matched_paths)
        result = {"design_brief": brief} | prepare(
            graph, matches, task, reviews=review_rows, delivery_advice=delivery.advice(self, graph)
        )
        result["baseline_id"] = pinned
        baseline = (
            graph if pinned == graph.snapshot.id else self.store.load_graph(str(self.root), pinned)
        )
        result["historical_associations"] = self._history_advice(
            graph,
            {owner["path"] for owner in result["likely_owners"]},
            baseline.snapshot.revision if baseline else None,
        )
        result["task_id"] = self.evidence.start_task(
            task, graph.snapshot.revision, graph.snapshot.id, pinned, continue_task=continue_task
        )
        self.evidence.record(
            "context_supplied",
            {
                "snapshot_id": graph.snapshot.id,
                "operation": "prepare_change",
                "entity_ids": [item["id"] for item in result["likely_owners"]],
            },
        )
        return result

    def assess_plan(
        self, task: str, files: list[str], decisions: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        if not isinstance(files, list) or not files or len(files) > 30:
            raise ValueError("files must name between 1 and 30 relative paths")
        if any(
            not isinstance(p, str) or Path(p).is_absolute() or ".." in Path(p).parts for p in files
        ):
            raise ValueError("Plan files must be repository-relative paths")
        brief = self.prepare_change(task, continue_task=True)
        closed, findings = _plan_findings(self._current(), brief, files)
        architecture = component_architecture.assess(self, brief, files, decisions, closed)
        return {
            "snapshot_id": brief["snapshot_id"],
            "files": files,
            "findings": findings,
            "brief": brief,
            "status": architecture["status"]
            if architecture["status"] in {"decision_required", "scope_incomplete"}
            else "advisory",
            "architecture": architecture,
            "proposed_ci_plan": delivery.advisory(self, changed_paths=files),
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
        basis = (
            "explicit"
            if baseline_id
            else ("pinned-change-baseline" if stats.get("pinned_snapshot_id") else "previous-scan")
        )
        result = change_review(graph, previous, receipts) | {
            "baseline_id": selected,
            "baseline_basis": basis,
            "comparison": {
                "explicit": "explicit baseline",
                "pinned-change-baseline": "pinned change baseline",
                "previous-scan": "previous scan" if selected else "no saved baseline",
            }[basis],
        }

        result["architecture"] = component_architecture.followthrough(self, selected)
        result["extension_points"] = _extension_review(graph, previous)
        result["historical_associations"] = self._history_advice(
            graph,
            set(changed_paths(graph, previous)),
            previous.snapshot.revision if previous else None,
        )
        result["ci_plan"] = delivery.advisory(
            self, base_snapshot_id=baseline_id or stats.get("pinned_snapshot_id")
        )
        self.evidence.outcome(
            "validation_reported",
            graph.snapshot.id,
            receipts,
            "Supplied checks and structural comparison; not independent correctness proof.",
        )
        return result

    def ci_plan(
        self,
        *,
        base_ref: str | None = None,
        base_snapshot_id: str | None = None,
        changed_paths: list[str] | None = None,
        cadence: str = "change",
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return delivery.plan(
            self,
            base_ref=base_ref,
            base_snapshot_id=base_snapshot_id,
            changed_paths=changed_paths,
            cadence=cadence,
            runtime_context=runtime_context,
        )

    def trial_evidence(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return {"summary": self.evidence.summary(), "events": self.evidence.events(limit, offset)}

    def export_evidence(self) -> dict[str, Any]:
        return self.evidence.export()

    analysis_request = review_operations.analysis_request
    submit_analysis = review_operations.submit_analysis
    reviews = review_operations.reviews
    _review_state = review_operations._review_state
    _rebuild_request = review_operations._rebuild_request
    analysis_product_request = review_operations.analysis_product_request
    analysis_saved = review_operations.analysis_saved
    analysis_selection = review_operations.analysis_selection
    analysis_reconcile = review_operations.analysis_reconcile
    portable_status = records.portable_status
    _portable_token = records._portable_token
    export_record = records.export_record
    import_record = records.import_record

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

    def workflow_status(
        self, run_id: str | None = None, batch_offset: int = 0, batch_limit: int = 10
    ) -> dict[str, Any]:
        return self._workflows.status(run_id, batch_offset=batch_offset, batch_limit=batch_limit)

    def workflow_resume(self, run_id: str, wait: bool = False) -> dict[str, Any]:
        return self._workflows.resume(run_id, background=not wait)

    def workflow_configure(self, config: dict | None = None) -> dict[str, Any]:
        return self._workflows.configuration(config)
