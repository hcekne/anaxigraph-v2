"""Frozen task specifications; a proposed track is not an executed benchmark.

Only Switchboard currently has a seed, public behavior contracts and cumulative
external acceptance checks. The two imported proposals deliberately remain drafts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRACK_IDS = ("switchboard", "chronoweave", "aetherloom")


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def validate_specification(value: dict) -> dict:
    fields = {
        "schema_version",
        "id",
        "version",
        "name",
        "status",
        "evidence_kind",
        "readiness_note",
        "source",
        "steps",
        "oracle_ideas",
        "post_history_probes",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Draft track must contain only supported specification fields")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or type(value["version"]) is not int
        or value["version"] < 1
        or value["id"] not in TRACK_IDS[1:]
        or value["status"] != "specification_only"
        or value["evidence_kind"] != "unexecuted_specification"
        or value["post_history_probes"] != []
    ):
        raise ValueError("Imported tracks must remain explicitly unexecuted specifications")
    if any(
        not isinstance(value[key], str) or not value[key].strip()
        for key in ("name", "readiness_note", "source")
    ):
        raise ValueError("Draft track requires its name, origin and readiness limitation")
    steps = value["steps"]
    if not isinstance(steps, list) or len(steps) != 21:
        raise ValueError("Draft tracks contain initial design plus twenty requests")
    for number, step in enumerate(steps):
        required = {"number", "phase", "title", "prompt", "architectural_pressure"}
        if (
            not isinstance(step, dict)
            or not required <= step.keys()
            or set(step) - required - {"probe_id"}
            or type(step["number"]) is not int
            or step["number"] != number
            or any(
                not isinstance(step[key], str) or not step[key].strip()
                for key in required - {"number"}
            )
        ):
            raise ValueError("Each draft request needs its exact ordered public specification")
    if [(step["number"], step["probe_id"]) for step in steps if "probe_id" in step] != [
        (7, "P1"),
        (14, "P2"),
        (20, "P3"),
    ]:
        raise ValueError("Inline fault probes must remain P1/P2/P3 at steps 7/14/20")
    ideas = value["oracle_ideas"]
    if (
        not isinstance(ideas, list)
        or len(ideas) != 3
        or any(not isinstance(idea, str) or not idea.strip() for idea in ideas)
    ):
        raise ValueError("Draft oracle ideas are three prose hypotheses, not test results")
    return value


def describe(track_id: str = "switchboard") -> dict:
    if track_id not in TRACK_IDS:
        raise ValueError(f"Unknown track: {track_id}")
    if track_id != "switchboard":
        value = validate_specification(
            json.loads((HERE / "tracks" / f"{track_id}.json").read_text())
        )
        if value["id"] != track_id:
            raise ValueError("Track file identity does not match the requested track")
        return value
    root = HERE / "scenario"
    return {
        "schema_version": 1,
        "id": track_id,
        "version": 1,
        "name": "Switchboard",
        "status": "executable",
        "evidence_kind": "scenario_definition",
        "readiness_note": "Executable external acceptance checks exist; this catalog is not a run result.",
        "steps": [
            {
                "number": int(path.name[:2]),
                "title": path.stem,
                "prompt": path.read_text(),
                "acceptance_file": f"step_{int(path.name[:2]):02d}.py",
            }
            for path in sorted((root / "requests").glob("*.md"))
        ],
        "post_history_probes": [
            {"id": path.stem, "prompt": path.read_text(), "acceptance_file": f"probe_{n}.py"}
            for n, path in enumerate(sorted((root / "probes").glob("*.md")), 1)
        ],
    }


def requirements(track: dict) -> dict:
    """Small frozen completeness contract saved with each chain and used by reporting."""
    return {
        "id": track["id"],
        "version": track["version"],
        "status": track["status"],
        "steps": [step["number"] for step in track["steps"]],
        "inline_probes": [
            {"step": step["number"], "id": step["probe_id"]}
            for step in track["steps"]
            if "probe_id" in step
        ],
        "post_history_probes": [probe["id"] for probe in track["post_history_probes"]],
        "comparison_rule": "switchboard-v1" if track["id"] == "switchboard" else None,
    }


def acceptance_files(scenario: dict, through: int) -> list[str]:
    return [f"step_{number:02d}.py" for number in scenario["steps"] if number <= through]


def file_hashes(root: Path) -> dict:
    caches = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".uv-cache"}
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not caches.intersection(path.relative_to(root).parts)
    }


def identity(track: dict) -> dict:
    seed, oracle = {}, None
    if track["status"] == "executable":
        root = HERE / "scenario"
        seed = file_hashes(root / "seed")
        oracle = digest(
            {
                "hidden": file_hashes(root / "hidden"),
                "hub": hashlib.sha256((root / "hub.py").read_bytes()).hexdigest(),
            }
        )
    return {
        "id": track["id"],
        "version": track["version"],
        "scenario_sha256": digest({"specification": track, "seed": seed}),
        "requests_sha256": digest(
            {"steps": track["steps"], "probes": track["post_history_probes"]}
        ),
        "oracle_sha256": oracle,
    }


def export(track: dict, destination: Path) -> None:
    """Never overwrite an existing catalog or confuse an export with trial evidence."""
    payload = {"track": track, "scenario_identity": identity(track)}
    with destination.open("x") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
        stream.write("\n")
