"""Reject incomplete, oversized or ungrounded agent output before saving it."""

from __future__ import annotations

import json
from functools import partial, wraps
from typing import Any, Callable, TypeVar, cast

from anaxigraph.contracts import Graph

from .architecture import FINDING_FIELDS, validate_architecture
from .behavior import check_annotation_behaviors
from .delivery import canonical_delivery, validate_delivery
from .fields import text, texts
from .packets import DEFAULT_RESULT_BYTES, _check_behavior

_Validator = TypeVar("_Validator", bound=Callable[..., Any])


class ModelOutputError(ValueError):
    """The supplied model output failed its contract; another worker may repair it."""

    model_request_id: str | None = None


def _model_output(operation: _Validator) -> _Validator:
    @wraps(operation)
    def checked(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except ValueError as error:
            request = args[0] if args else kwargs["request"]
            failure = ModelOutputError(str(error))
            failure.model_request_id = request.get("request_id")
            raise failure from error

    return cast(_Validator, checked)


def _project(value: Any, fields: set[str], children: dict[str, Any] | None = None) -> Any:
    """Keep malformed required values intact for the normal validator to reject."""
    if not isinstance(value, dict):
        return value
    result = {key: item for key, item in value.items() if key in fields}
    for key, projection in (children or {}).items():
        if key in result and isinstance(result[key], list):
            result[key] = [projection(item) for item in result[key]]
    return result


def _execution_worker(value: Any, *, fallbacks: bool = False) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("provider"), str)
        or value["provider"] not in {"codex", "claude"}
    ):
        raise ValueError("Execution worker provider must be codex or claude")
    model = value.get("model")
    if not isinstance(model, str) or len(model) > 150:
        raise ValueError("Execution worker model must be text of at most 150 characters")
    worker = {"provider": value["provider"], "model": model}
    effort = value.get("reasoning_effort")
    if effort is not None:
        if value["provider"] != "codex" or effort not in ("low", "medium", "high", "xhigh"):
            raise ValueError("Execution reasoning_effort must be a supported Codex reasoning level")
        worker["reasoning_effort"] = effort
    if fallbacks and "fallbacks" in value:
        choices = value["fallbacks"]
        if not isinstance(choices, list) or len(choices) > 4:
            raise ValueError("Execution permits at most four fallback workers")
        worker["fallbacks"] = [_execution_worker(choice) for choice in choices]
    return worker


def _execution(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("pipeline"), dict):
        raise ValueError("Execution provenance needs a model pipeline")
    pipeline = value["pipeline"]

    def reviewers(choices):
        if not isinstance(choices, list) or len(choices) > 4:
            raise ValueError("Execution permits at most four ordered review passes")
        return [_execution_worker(choice) for choice in choices]

    return {
        "pipeline": {
            "worker": _execution_worker(pipeline.get("worker"), fallbacks=True),
            "review_passes": reviewers(pipeline.get("review_passes")),
        },
        "worker": _execution_worker(value.get("worker")),
        "review_passes": reviewers(value.get("review_passes")),
    }


def _validate_execution(value: dict[str, Any]) -> None:
    if value.get("execution") is not None:
        _execution(value["execution"])


@_model_output
def canonical_result(request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Project validated model output onto the portable analysis contract.

    Raw source, cache fragments and unknown diagnostics have no place in saved
    understanding. This projection does not establish correctness: call
    ``validate_result`` too. Required fields are never invented or defaulted.
    """

    def reference(item):
        return _project(item, {"path", "line"})

    def annotation(item):
        projected = _project(
            item,
            {
                "node_id",
                "overview",
                "detail",
                "capabilities",
                "functions",
                "contracts",
                "uncertainty",
            },
            {
                "functions": lambda function: _project(function, {"node_id", "overview"}),
                "contracts": lambda contract: _project(
                    contract, {"statement", "basis", "evidence"}, {"evidence": reference}
                ),
            },
        )
        return projected

    kind = request["kind"]
    if kind == "delivery":
        canonical = canonical_delivery(result)
        if result.get("execution") is not None:
            canonical["execution"] = _execution(result["execution"])
        return canonical
    fields, children = {
        "annotate": ({"producer", "annotations"}, {"annotations": annotation}),
        "pattern-screen": (
            {"producer", "selections"},
            {"selections": lambda item: _project(item, {"module_id", "pattern_ids"})},
        ),
        "patterns": (
            {"producer", "evaluations"},
            {
                "evaluations": lambda item: _project(
                    item,
                    {
                        "module_id",
                        "pattern_id",
                        "opportunity",
                        "current_fit",
                        "proposed_change",
                        "pros",
                        "cons",
                        "alternative",
                        "evidence",
                    },
                    {"evidence": reference},
                )
            },
        ),
        "architecture": (
            {"producer", "lens", "assessments", "findings", "uncertainty"},
            {
                "assessments": lambda item: _project(item, {"module_id", "outcome"}),
                "findings": lambda item: _project(
                    item,
                    FINDING_FIELDS,
                    {
                        "evidence": reference,
                        "options": lambda option: _project(
                            option, {"placement", "benefit", "cost"}
                        ),
                    },
                ),
            },
        ),
        "fresh-eyes": (
            {
                "producer",
                "alternatives",
                "functional_summary",
                "functional_requirements",
                "functional_coverage",
            },
            {
                "alternatives": lambda item: _project(
                    item, {"name", "design", "requirements", "pros", "cons", "diagram"}
                )
            },
        ),
    }.get(kind, (None, None))
    if fields is None:
        raise ValueError("Unknown analysis result kind")
    canonical = _project(result, fields, children)
    if not isinstance(canonical, dict):
        raise ValueError("Analysis result must be an object")
    if result.get("execution") is not None:
        canonical["execution"] = _execution(result["execution"])
    return canonical


_text = text
_texts = partial(texts, count=30, limit=500)


def _evidence(value: Any, graph: Graph, *, required: bool = False) -> None:
    if not isinstance(value, list) or len(value) > 30 or (required and not value):
        raise ValueError("Evidence must be a bounded list of source references")
    for reference in value:
        if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
            raise ValueError("Evidence path must be text")
        if reference["path"] not in graph.snapshot.files:
            raise ValueError("Evidence path must exist in the analyzed snapshot")
        if type(reference.get("line")) is not int or reference["line"] < 1:
            raise ValueError("Evidence line must be a positive integer")
        module = next(
            (n for n in graph.nodes if n.kind == "module" and n.path == reference["path"]), None
        )
        if module and reference["line"] > max(
            1, module.data.get("physical_lines", module.data.get("loc", 0))
        ):
            raise ValueError("Evidence line lies outside the mapped source file")


def _identities(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, dict) or not isinstance(item.get(field), str) for item in value
    ):
        raise ValueError(f"Every item requires a text {field}")
    return [item[field] for item in value]


def _capabilities(annotation: dict[str, Any], graph: Graph) -> None:
    module = next(node for node in graph.nodes if node.id == annotation["node_id"])
    _texts(
        annotation.get("capabilities"),
        "capabilities",
        required=module.data.get("inert_module") is not True,
    )


def _annotations(request: dict[str, Any], result: dict[str, Any], graph: Graph) -> None:
    annotations = result.get("annotations")
    if not isinstance(annotations, list) or len(annotations) != len(request["scope"]):
        raise ValueError("Annotation result must cover every requested module exactly once")
    if set(_identities(annotations, "node_id")) != set(request["scope"]):
        raise ValueError("Annotation result has missing, duplicate or unexpected module IDs")
    for annotation in annotations:
        _validate_execution(annotation)
        _text(annotation.get("overview"), "overview", 320)
        _text(annotation.get("detail"), "detail", 1500)
        _capabilities(annotation, graph)
        _texts(annotation.get("uncertainty"), "uncertainty")
        functions = annotation.get("functions")
        expected = set(request["function_ids"][annotation["node_id"]])
        identities = _identities(functions, "node_id")
        supplied = set(identities)
        missing, unexpected = expected - supplied, supplied - expected
        duplicates = len(identities) - len(supplied)
        if missing or unexpected or duplicates:
            raise ValueError(
                f"Function coverage IDs: expected={len(expected)} received={len(identities)} "
                f"missing={len(missing)} duplicate={duplicates} unexpected={len(unexpected)}; "
                f"module={annotation['node_id']}; "
                f"missing_ids={sorted(missing)[:3]}; unexpected_ids={sorted(unexpected)[:3]}"
            )
        for function in functions:
            _text(function.get("overview"), "function overview", 320)
        contracts = annotation.get("contracts")
        if not isinstance(contracts, list) or len(contracts) > 30:
            raise ValueError("contracts must be a bounded list")
        for contract in contracts:
            if not isinstance(contract, dict):
                raise ValueError("Each contract must be an object")
            _text(contract.get("statement"), "contract statement", 600)
            if not isinstance(contract.get("basis"), str) or contract["basis"] not in {
                "declared",
                "inferred",
            }:
                raise ValueError("Contracts must distinguish declared from inferred basis")
            _evidence(contract.get("evidence"), graph, required=True)
    check_annotation_behaviors(graph, annotations)


def _pattern_screen(request: dict[str, Any], result: dict[str, Any], graph: Graph) -> None:
    selections = result.get("selections")
    if not isinstance(selections, list) or len(selections) != len(request["scope"]):
        raise ValueError("Screening must cover every requested module exactly once")
    if set(_identities(selections, "module_id")) != set(request["scope"]):
        raise ValueError("Screening has missing, duplicate or unexpected module IDs")
    catalogue_ids = {card["id"] for card in request["packet"]["catalogue"]}
    for selection in selections:
        ids = selection.get("pattern_ids")
        if not isinstance(ids, list) or len(ids) > 10 or any(not isinstance(i, str) for i in ids):
            raise ValueError("Screen selections must contain zero to ten catalogue IDs")
        if len(set(ids)) != len(ids) or set(ids) - catalogue_ids:
            raise ValueError("Screen selections contain duplicate or unknown catalogue IDs")


def _patterns(request: dict[str, Any], result: dict[str, Any], graph: Graph) -> None:
    evaluations = result.get("evaluations")
    patterns = {card["id"] for card in request["packet"]["catalogue"]}
    expected_pairs = {(module, pattern) for module in request["scope"] for pattern in patterns}
    if not isinstance(evaluations, list) or len(evaluations) != len(expected_pairs):
        raise ValueError("Every requested module/catalogue pair must be evaluated exactly once")
    actual = set(zip(_identities(evaluations, "module_id"), _identities(evaluations, "pattern_id")))
    if actual != expected_pairs:
        raise ValueError("Pattern coverage contains duplicate, missing or unexpected pairs")
    for evaluation in evaluations:
        if "opportunity" not in evaluation:
            raise ValueError(
                "Every pattern needs an explicit opportunity rating or null for unknown"
            )
        score = evaluation.get("opportunity")
        if score is not None and (type(score) is not int or not 0 <= score <= 3):
            raise ValueError("Opportunity must be an integer 0–3 or null for insufficient evidence")
        _texts(evaluation.get("pros"), "pros", required=True)
        _texts(evaluation.get("cons"), "cons", required=True)
        _text(evaluation.get("alternative"), "alternative", 600)
        _evidence(evaluation.get("evidence"), graph, required=bool(score))
        if score or "current_fit" in evaluation:
            if not isinstance(evaluation.get("current_fit"), str) or evaluation[
                "current_fit"
            ] not in {"present", "partial", "absent", "unknown"}:
                raise ValueError("Positive opportunities need an explicit current_fit")
        if score or "proposed_change" in evaluation:
            _text(evaluation.get("proposed_change"), "proposed_change", 1000)


def _functional_coverage(result, expected):
    coverage, brief = result["functional_coverage"], result.get("functional_requirements")
    if (
        not isinstance(brief, list)
        or not isinstance(coverage, list)
        or len(coverage) != len(brief)
        or any(
            not isinstance(group, list)
            or not group
            or any(not isinstance(item, str) for item in group)
            for group in coverage
        )
    ):
        raise ValueError(
            "functional_coverage must align requirement IDs with functional_requirements"
        )
    if set(item for group in coverage for item in group) != expected:
        raise ValueError("Functional brief coverage must preserve every requirement")


def _fresh_eyes(request: dict[str, Any], result: dict[str, Any], graph: Graph) -> None:
    expected = {item["id"] for item in request["packet"]["requirements"]}
    fields = ("functional_summary", "functional_requirements", "functional_coverage")
    missing = set(fields).intersection(request.get("response_schema", {})).difference(result)
    if missing:
        raise ValueError(
            "Fresh Eyes missing required behavior brief fields: " + ", ".join(sorted(missing))
        )
    if "functional_summary" in result:
        _check_behavior(graph, _text(result["functional_summary"], "functional_summary", 600))
    if "functional_requirements" in result:
        brief = result["functional_requirements"]
        if not isinstance(brief, list) or not 1 <= len(brief) <= 8:
            raise ValueError("functional_requirements needs one to eight behavior statements")
        for statement in brief:
            _check_behavior(graph, _text(statement, "functional requirement", 600))
    if "functional_coverage" in result:
        _functional_coverage(result, expected)
    alternatives = result.get("alternatives")
    if not isinstance(alternatives, list) or len(alternatives) != 3:
        raise ValueError("Fresh Eyes requires three alternatives, retained independently")
    designs = set()
    names = set()
    for alternative in alternatives:
        if not isinstance(alternative, dict):
            raise ValueError("Each alternative must be an object")
        names.add(_text(alternative.get("name"), "name", 160))
        designs.add(_text(alternative.get("design"), "design", 6000))
        coverage = alternative.get("requirements")
        if not isinstance(coverage, list) or any(not isinstance(x, str) for x in coverage):
            raise ValueError("Requirement coverage must be a list of requirement IDs")
        if (
            not isinstance(coverage, list)
            or len(coverage) != len(expected)
            or set(coverage) != expected
        ):
            raise ValueError("Each design must explicitly preserve every requirement")
        _texts(alternative.get("pros"), "pros", required=True)
        _texts(alternative.get("cons"), "cons", required=True)
        diagram = _text(alternative.get("diagram"), "diagram", 6000)
        if not diagram.startswith(("flowchart ", "graph ")):
            raise ValueError("Each alternative needs a high-level Mermaid flowchart")
    if len(names) != 3 or len(designs) != 3:
        raise ValueError("Alternatives must have distinct names and design descriptions")


@_model_output
def validate_result(request: dict[str, Any], result: dict[str, Any], graph: Graph) -> None:
    """Validation establishes shape and coverage, not truth or model originality."""
    maximum = request.get("max_result_bytes", DEFAULT_RESULT_BYTES)
    if type(maximum) is not int or maximum < 1024:
        raise ValueError("Analysis result budget must be an integer of at least 1024 bytes")
    if not isinstance(result, dict) or len(json.dumps(result, allow_nan=False).encode()) > maximum:
        raise ValueError("Analysis result exceeds its configured batch budget")
    _text(result.get("producer"), "producer", 200)
    _validate_execution(result)
    kind = request["kind"]
    validators = {
        "annotate": _annotations,
        "pattern-screen": _pattern_screen,
        "patterns": _patterns,
        "fresh-eyes": _fresh_eyes,
        "architecture": lambda request, result, graph: validate_architecture(
            request, result, graph, _evidence
        ),
        "delivery": validate_delivery,
    }
    if kind not in validators:
        raise ValueError("Unknown analysis result kind")
    validators[kind](request, result, graph)
