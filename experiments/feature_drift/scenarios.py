"""Frozen task specifications; a proposed track is not an executed benchmark.

Switchboard lives in `scenario/`. An imported track becomes executable only when
`tracks/<id>/` holds its seed, all 21 public requests and a hidden acceptance file per
request; until then it remains the draft specification in `tracks/<id>.json`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Tracks authored in the separate anaxi_evals suite: `evals/<id>/track/`. They declare their
# own step count, carry no inline probes and keep post-history probes beside the requests.
EXTERNAL_TRACK_IDS = ("relaydesk", "dispatchledger", "caseflow")
TRACK_IDS = ("switchboard", "chronoweave", "aetherloom")
ALL_TRACK_IDS = (*TRACK_IDS, *EXTERNAL_TRACK_IDS)
COMPARISON_RULES = {track: f"{track}-v1" for track in ALL_TRACK_IDS}


def evals_root() -> Path:
    return Path(os.environ.get("ANAXI_EVALS_ROOT", HERE.parents[2] / "anaxi_evals")).resolve()


def root(track_id: str) -> Path:
    """Directory holding the track's seed, requests and hidden acceptance checks."""
    if track_id in EXTERNAL_TRACK_IDS:
        return evals_root() / "evals" / track_id / "track"
    return HERE / "scenario" if track_id == "switchboard" else HERE / "tracks" / track_id


def _step_count(base: Path) -> int:
    meta = base / "track.json"
    return json.loads(meta.read_text()).get("step_count", 21) if meta.is_file() else 21


def _complete(track_id: str) -> bool:
    base = root(track_id)
    requests = sorted((base / "requests").glob("*.md"))
    count = _step_count(base)
    return (
        (base / "track.json").is_file()
        and (base / "seed" / "run.json").is_file()
        and [int(path.name[:2]) for path in requests] == list(range(count))
        and all((base / "hidden" / f"step_{n:02d}.py").is_file() for n in range(count))
    )


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


def draft(track_id: str) -> dict:
    """The imported prose specification, kept as the origin of an executable track."""
    value = validate_specification(json.loads((HERE / "tracks" / f"{track_id}.json").read_text()))
    if value["id"] != track_id:
        raise ValueError("Track file identity does not match the requested track")
    return value


def _executable(track_id: str) -> dict:
    base = root(track_id)
    meta = json.loads((base / "track.json").read_text())
    probes = {int(step): name for step, name in meta["inline_probes"].items()}
    external = track_id in EXTERNAL_TRACK_IDS
    if meta.get("id") != track_id or (
        probes != {7: "P1", 14: "P2", 20: "P3"} and not (external and probes == {})
    ):
        raise ValueError("Executable track metadata must keep its identity and P1/P2/P3 at 7/14/20")
    steps = []
    for path in sorted((base / "requests").glob("*.md")):
        number = int(path.name[:2])
        step = {"number": number, "title": path.stem, "prompt": path.read_text(),
                "acceptance_file": f"step_{number:02d}.py"}  # fmt: skip
        if number in probes:
            step["probe_id"] = probes[number]
        steps.append(step)
    return {
        "schema_version": 1,
        "id": track_id,
        "version": meta["version"],
        "name": meta["name"],
        "product": meta["product"],
        "status": "executable",
        "evidence_kind": "scenario_definition",
        "readiness_note": "Executable external acceptance checks exist; adapted from the draft "
        "specification as listed in adaptations. This catalog is not a run result.",
        "ci_from_step": meta["ci_from_step"],
        "adaptations": meta.get("adaptations", []),
        "draft_sha256": digest(
            (base.parent / "spec.md").read_text() if external else draft(track_id)
        ),
        "steps": steps,
        "post_history_probes": [
            {"id": path.stem, "prompt": path.read_text(), "acceptance_file": f"probe_{n}.py"}
            for n, path in enumerate(sorted((base / "probes").glob("*.md")), 1)
        ]
        if external
        else [],
    }


def describe(track_id: str = "switchboard") -> dict:
    if track_id not in ALL_TRACK_IDS:
        raise ValueError(f"Unknown track: {track_id}")
    if track_id in EXTERNAL_TRACK_IDS and not _complete(track_id):
        raise ValueError(f"{track_id} has no complete executable track under {root(track_id)}")
    if track_id != "switchboard":
        return _executable(track_id) if _complete(track_id) else draft(track_id)
    base = root(track_id)
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
            for path in sorted((base / "requests").glob("*.md"))
        ],
        "post_history_probes": [
            {"id": path.stem, "prompt": path.read_text(), "acceptance_file": f"probe_{n}.py"}
            for n, path in enumerate(sorted((base / "probes").glob("*.md")), 1)
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
        "comparison_rule": COMPARISON_RULES[track["id"]]
        if track["status"] == "executable"
        else None,
    }


def layout(track: dict) -> dict:
    """Runtime inputs: where the seed, requests and checks live, and how prompts name it."""
    base = root(track["id"])
    meta = base / "track.json"
    limit = json.loads(meta.read_text()).get("agent_timeout", 5400) if meta.is_file() else 5400
    return {
        "agent_timeout": limit,
        "seed": base / "seed",
        "hidden": base / "hidden",
        "requests": sorted((base / "requests").glob("*.md")),
        "probes": sorted((base / "probes").glob("*.md")),
        "product": track.get("product", track["name"]),
        "ci_from_step": track.get("ci_from_step", 6),
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
        base = root(track["id"])
        seed = file_hashes(base / "seed")
        hub = base / "hub.py"
        oracle = digest(
            {
                "hidden": file_hashes(base / "hidden"),
                "hub": hashlib.sha256(hub.read_bytes()).hexdigest() if hub.exists() else None,
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
