"""Prepare blind subagent sheets, freeze judgments, then join separate trial outcomes.

Example: python -m experiments.architecture_quality.review prepare --help
These files establish this tool's ordering and hashes, not that a reviewer never
read private assignments. Give reviewers only anonymous bundles, rubric and sheet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path

CONTRACT = "architecture-judgment-v1"
DIMENSIONS = (
    "behavioral_contracts",
    "responsibility_private_boundaries",
    "independent_verification",
    "ci_scope_assurance",
    "later_change_difficulty",
    "simplicity",
)
RUBRIC = Path(__file__).with_name("rubric.md")


def read(path):
    return json.loads(Path(path).read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def save_new(path, value):
    path = Path(path)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def bundle_hashes(root):
    root = Path(root)
    result = {}
    for candidate in sorted(root.glob("candidate-*")):
        if not candidate.is_dir() or candidate.is_symlink():
            raise ValueError("Anonymous candidates must be ordinary directories")
        files = {}
        for path in sorted(candidate.rglob("*")):
            if path.is_symlink():
                raise ValueError("Review bundles cannot contain symlinks")
            if path.is_file():
                files[path.relative_to(candidate).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        if not {"tasks.json", "outcomes.json"} <= files.keys():
            raise ValueError("Every candidate needs task and independent outcome evidence")
        result[candidate.name] = files
    if not result:
        raise ValueError("No anonymous candidate bundles found")
    return result


def object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def schema(candidates, hashes):
    text = {"type": "string", "minLength": 1}
    evidence = object_schema({"path": text, "quote": text, "reason": text})
    score = object_schema(
        {
            "score": {"type": "integer", "minimum": 0, "maximum": 3},
            "evidence": {"type": "array", "minItems": 1, "items": evidence},
        }
    )
    review = object_schema(
        {
            "candidate": {"enum": list(candidates)},
            "dimensions": object_schema(dict.fromkeys(DIMENSIONS, score)),
            "uncertainty": object_schema(
                {"level": {"enum": ["low", "medium", "high"]}, "reason": text}
            ),
            "suspected_unblinding": object_schema(
                {
                    "suspected": {"type": "boolean"},
                    "explanation": text,
                    "evidence": {"type": "array", "items": evidence},
                }
            ),
            "summary": text,
        }
    )
    result = object_schema(
        {
            "contract": {"const": CONTRACT},
            "reviewer": object_schema(
                {
                    "id": text,
                    "kind": {"enum": ["human", "model"]},
                    "model": {"type": ["string", "null"]},
                    "reasoning_effort": {"type": ["string", "null"]},
                }
            ),
            "artifact_sha256": {"const": hashes},
            "rubric_sha256": {"const": hashlib.sha256(RUBRIC.read_bytes()).hexdigest()},
            "reviews": {
                "type": "array",
                "minItems": len(candidates),
                "maxItems": len(candidates),
                "items": review,
            },
        }
    )
    result["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    result["description"] = (
        "Complete every anonymous candidate exactly once. Evidence quotes must occur in the "
        "named candidate file. Models require model and effort; humans require null for both. "
        "Suspected unblinding requires evidence. No aggregate score or preference is inferred."
    )
    return result


def prepare(blind_root, output):
    hashes = bundle_hashes(blind_root)
    output = Path(output)
    output.mkdir(exist_ok=False)
    sheet = {
        "contract": CONTRACT,
        "reviewer": dict.fromkeys(("id", "kind", "model", "reasoning_effort")),
        "artifact_sha256": hashes,
        "rubric_sha256": hashlib.sha256(RUBRIC.read_bytes()).hexdigest(),
        "reviews": [
            {
                "candidate": candidate,
                "dimensions": {key: {"score": None, "evidence": []} for key in DIMENSIONS},
                "uncertainty": {"level": None, "reason": None},
                "suspected_unblinding": {"suspected": None, "explanation": None, "evidence": []},
                "summary": None,
            }
            for candidate in hashes
        ],
    }
    save_new(output / "sheet.json", sheet)
    save_new(output / "schema.json", schema(hashes, hashes))
    (output / "rubric.md").write_bytes(RUBRIC.read_bytes())
    return sheet


def check_schema(value, specification, location="sheet"):
    """Validate the small JSON Schema vocabulary emitted above, without a dependency."""
    if "const" in specification and value != specification["const"]:
        raise ValueError(f"{location}: differs from frozen value")
    if "enum" in specification and value not in specification["enum"]:
        raise ValueError(f"{location}: choose an explicit permitted value")
    kind = specification.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
    if kind is not None and not any(type(value) is types[item] for item in kinds):
        raise ValueError(f"{location}: expected {kind}")
    if isinstance(value, dict) and kind == "object":
        if set(value) != set(specification["required"]):
            raise ValueError(f"{location}: missing or unexpected fields")
        for key, child in specification["properties"].items():
            check_schema(value[key], child, location + "." + key)
    if isinstance(value, list) and kind == "array":
        if (
            not specification.get("minItems", 0)
            <= len(value)
            <= specification.get("maxItems", len(value))
        ):
            raise ValueError(f"{location}: wrong item count")
        for index, child in enumerate(value):
            check_schema(child, specification["items"], f"{location}[{index}]")
    if type(value) is int and not specification["minimum"] <= value <= specification["maximum"]:
        raise ValueError(f"{location}: score must be 0..3")
    if isinstance(value, str) and "minLength" in specification and not value.strip():
        raise ValueError(f"{location}: nonempty text required")


def validate(sheet, blind_root):
    hashes = bundle_hashes(blind_root)
    check_schema(sheet, schema(hashes, hashes))
    reviewer = sheet["reviewer"]
    for field in ("model", "reasoning_effort"):
        value = reviewer[field]
        if reviewer["kind"] == "model" and (not isinstance(value, str) or not value.strip()):
            raise ValueError("Model reviewers must identify model and reasoning effort")
        if reviewer["kind"] == "human" and value is not None:
            raise ValueError("Human reviewer model and effort must be null")
    if sorted(row["candidate"] for row in sheet["reviews"]) != sorted(hashes):
        raise ValueError("Review every anonymous candidate exactly once")
    for row in sheet["reviews"]:
        unblinding = row["suspected_unblinding"]
        if unblinding["suspected"] and not unblinding["evidence"]:
            raise ValueError("Suspected unblinding needs source evidence")
        evidence = unblinding["evidence"] + [
            item for dimension in row["dimensions"].values() for item in dimension["evidence"]
        ]
        for item in evidence:
            if item["path"] not in hashes[row["candidate"]]:
                raise ValueError("Evidence must name a file in its anonymous candidate")
            source = (Path(blind_root) / row["candidate"] / item["path"]).read_text()
            if item["quote"] not in source:
                raise ValueError("Evidence quote does not occur in the named file")


def freeze(blind_root, sheet_path, output):
    sheet = read(sheet_path)
    validate(sheet, blind_root)
    record = {
        "contract": CONTRACT + "-frozen",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "judgment_sha256": digest(sheet),
        "schema_sha256": digest(schema(sheet["artifact_sha256"], sheet["artifact_sha256"])),
        "judgment": sheet,
    }
    save_new(output, record)
    return record


def bind_trial(state, assignments, measured):
    """Bind anonymous artifacts to the trial's saved randomization and actual edits."""
    from . import run

    manifest = read(state / "run.private.json")
    invocation = read(state / "invocation.private.json")
    rng = random.Random(invocation["random_seed"])
    expected, slots = {}, {}
    for pair in manifest["pairs"]:
        sample, order, labels = run.pair_draw(rng)
        if sample != pair["hidden_sample_seed"] or set(pair["arms"]) != set(order):
            raise ValueError("Trial randomization or arm inventory changed")
        for arm in order:
            slot = (pair["scenario"], pair["replicate"], arm)
            if slot in slots:
                raise ValueError("Trial has duplicate scenario/replicate/arm outcomes")
            slots[slot] = pair["arms"][arm]
            expected[labels[arm]] = dict(zip(("scenario", "replicate", "arm"), slot))
    assigned = [(row["scenario"], row["replicate"], row["arm"]) for row in assignments.values()]
    measured_slots = [
        (pair["scenario"], pair["replicate"], arm)
        for pair in measured["pairs"]
        for arm in pair["arms"]
    ]
    if (
        len(set(assigned)) != len(assigned)
        or set(assigned) != set(measured_slots)
        or len(measured_slots) != len(slots)
        or set(measured_slots) != set(slots)
    ):
        raise ValueError("Assignments must be a bijection over the measured arm inventory")
    if assignments != expected:
        raise ValueError("Assignments differ from the saved trial randomization")
    if measured != run.comparison(manifest):
        raise ValueError("Comparison differs from private trial evidence")
    for candidate, assignment in assignments.items():
        scenario, replicate, arm = (assignment[key] for key in ("scenario", "replicate", "arm"))
        root = state / f"{scenario}-{replicate}" / arm
        bundle = state / "blind-review" / candidate
        source = {
            path: value
            for path, value in run.snapshot(bundle).items()
            if path not in {"outcomes.json", "tasks.json"} and not path.startswith("diffs/")
        }
        if not source or source != run.snapshot(root):
            raise ValueError("Reviewed source differs from its assigned trial candidate")
        rows = slots[(scenario, replicate, arm)]
        if read(bundle / "tasks.json") != run.blind_tasks(rows, root) or read(
            bundle / "outcomes.json"
        ) != run.blind_evidence([row["outcome"] for row in rows], root):
            raise ValueError("Reviewed tasks/outcomes differ from private trial evidence")
        evidence = root.parent / f"{arm}-evidence"
        for stage, row in enumerate(rows):
            directory = evidence / f"task-{stage}"
            baseline = read(directory / "baseline.private.json")
            next_baseline = evidence / f"task-{stage + 1}/baseline.private.json"
            after = read(next_baseline)["files"] if next_baseline.is_file() else source
            if (
                row["stage"] != stage
                or read(directory / "result.private.json") != row
                or digest(baseline["files"]) != baseline["digest"]
                or baseline["digest"] != row["baseline_digest"]
                or run.delta(baseline["files"], after) != row["patch"]
                or (bundle / f"diffs/task-{stage}.patch").read_text()
                != run.blind_diff(root, arm, stage)
            ):
                raise ValueError("Reviewed patches/baselines differ from private trial evidence")
    return {"trial_sha256": digest(manifest), "invocation_sha256": digest(invocation)}


def join(state, frozen_paths, output):
    state = Path(state)
    frozen, identities = [], set()
    # Validate every judgment before this tool reads either assignments or resources.
    for path in frozen_paths:
        record = read(path)
        sheet = record["judgment"]
        if record["contract"] != CONTRACT + "-frozen" or record["judgment_sha256"] != digest(sheet):
            raise ValueError("Frozen judgment changed")
        if not isinstance(record["frozen_at"], str) or not record["frozen_at"].strip():
            raise ValueError("Frozen judgment needs its timestamp")
        validate(sheet, state / "blind-review")
        if record["schema_sha256"] != digest(
            schema(sheet["artifact_sha256"], sheet["artifact_sha256"])
        ):
            raise ValueError("Frozen judgment schema changed")
        identity = sheet["reviewer"]["id"]
        if identity in identities:
            raise ValueError("Reviewer identities must be unique")
        identities.add(identity)
        frozen.append(record)
    if not frozen:
        raise ValueError("Freeze at least one review before joining assignments")
    assignments = read(state / "blind-map.private.json")
    measured = read(state / "comparison.json")
    if set(assignments) != set(frozen[0]["judgment"]["artifact_sha256"]):
        raise ValueError("Assignment labels differ from reviewed candidates")
    trial_binding = bind_trial(state, assignments, measured)
    candidates = []
    for candidate, assignment in assignments.items():
        matches = [
            pair
            for pair in measured["pairs"]
            if (pair["scenario"], pair["replicate"])
            == (assignment["scenario"], assignment["replicate"])
        ]
        if len(matches) != 1:
            raise ValueError("Each assignment needs exactly one measured pair")
        outcomes = matches[0]["arms"][assignment["arm"]]
        candidates.append(
            {
                "candidate": candidate,
                "assignment": assignment,
                "architecture": [
                    {
                        "reviewer": item["judgment"]["reviewer"],
                        "judgment_sha256": item["judgment_sha256"],
                        "review": next(
                            row
                            for row in item["judgment"]["reviews"]
                            if row["candidate"] == candidate
                        ),
                    }
                    for item in frozen
                ],
                "correctness": [
                    {
                        key: stage[key]
                        for key in (
                            "stage",
                            "behavior_passed",
                            "qualified",
                            "ci_safe",
                            "control_isolation",
                            "independent_components",
                        )
                        if key in stage
                    }
                    for stage in outcomes["stages"]
                ],
                "observations": [
                    {
                        key: stage[key]
                        for key in (
                            "stage",
                            "architecture_observations",
                            "changed_source_paths",
                            "advice_consulted_source_verified",
                        )
                        if key in stage
                    }
                    for stage in outcomes["stages"]
                ],
                "effort": {
                    **{
                        key: outcomes[key]
                        for key in ("coding", "analysis", "total_model_call_opportunities")
                    },
                    "stage_timings": [
                        {
                            key: stage[key]
                            for key in (
                                "stage",
                                "actual_evaluator_seconds",
                                "actual_candidate_check_seconds",
                            )
                            if key in stage
                        }
                        for stage in outcomes["stages"]
                    ],
                },
            }
        )
    report = {
        "contract": CONTRACT + "-joined",
        "evidence_kind": measured["evidence_kind"],
        "trial_status": measured["status"],
        "assignments_sha256": digest(assignments),
        "comparison_sha256": digest(measured),
        **trial_binding,
        "frozen_reviews_sha256": [digest(item) for item in frozen],
        "candidates": candidates,
        "interpretation": "Separate ordinal architecture judgments, correctness and measured effort. Reviewer kind is declared provenance. No summed score, inferred preference or causal advantage is claimed.",
        "ordering_limit": "This tool validates frozen judgments before reading assignments; it cannot prove reviewers never accessed private evidence.",
    }
    save_new(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "freeze", "join"):
        child = commands.add_parser(name)
        child.add_argument("--output", type=Path, required=True)
        if name == "join":
            child.add_argument("--state", type=Path, required=True)
            child.add_argument("--frozen", type=Path, nargs="+", required=True)
        else:
            child.add_argument("--blind-root", type=Path, required=True)
            if name == "freeze":
                child.add_argument("--sheet", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.blind_root, args.output)
    elif args.command == "freeze":
        freeze(args.blind_root, args.sheet, args.output)
    else:
        join(args.state, args.frozen, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
