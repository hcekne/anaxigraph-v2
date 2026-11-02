"""Explicitly execute a candidate's checks and write fingerprinted supplied CI evidence.

Run with this project's environment; the candidate needs .venv with pytest and pytest-cov.
This external qualification command is never called by the Application server.
Successful refreshes replace only the canonical current report; other outputs must be new.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

import metrics

from anaxigraph.analysis.ci_obligations import REPORT_PATH, audit_selection, evidence_inputs
from anaxigraph.indexing import IndexSession

MAX_BYTES = 1_000_000
_ENVIRONMENT = """
import hashlib, importlib.metadata, json, sys
packages = sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions())
print(json.dumps({'python': sys.version, 'distributions_sha256':
                  hashlib.sha256(json.dumps(packages, separators=(',', ':')).encode()).hexdigest()}))
"""


def safe_path(path: Path) -> Path:
    path = path.absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(f"Symlink paths are not allowed: {path}")
    return path.resolve()


def environment(repo: Path) -> dict:
    result = metrics.run(
        [str(repo / ".venv/bin/python"), "-c", _ENVIRONMENT], repo, 60, metrics.candidate_env(repo)
    )
    if result.returncode:
        raise ValueError("Candidate interpreter environment could not be fingerprinted")
    return json.loads(result.stdout)


def inventory(repo: Path) -> dict:
    return evidence_inputs(IndexSession(repo).snapshot().files)


def collect(repo: Path, targets: list[str]) -> list[str]:
    tests = metrics.collect(repo, targets)
    if tests is None or len(tests) != len(set(tests)):
        raise ValueError(f"Candidate test collection failed for {targets}")
    return sorted(tests)


def measure_diff(repo: Path, paths: list[str], tests: list[str]) -> dict:
    targets, _ = metrics.select(repo, paths)
    if targets is None:
        raise ValueError("Selector failed for the supplied change paths")
    selected = tests if targets == ["ALL"] else collect(repo, targets)
    full_passed, full_seconds = metrics.run_tests(repo, ["ALL"])
    selected_passed, selected_seconds = (
        metrics.run_tests(repo, targets) if selected else (None, 0.0)
    )
    return {
        "changed_paths": paths,
        "full_seconds": full_seconds,
        "selected_seconds": selected_seconds,
        "full_passed": full_passed,
        "selected_passed": selected_passed,
        "tests_selected": len(selected),
        "tests_total": len(tests),
        "basis": "Observed sequential runs in the same repository and environment; supplied change paths, not a universal or causal speed claim. Empty selection executes no assertions.",
    }


def references(values: list[str], indices: dict[str, int]) -> list[int]:
    try:
        return sorted({indices[value] for value in values})
    except KeyError as error:
        raise ValueError(f"Coverage or selection test ID was not collected: {error}") from error


def validate_shape(value: dict) -> None:
    audited = audit_selection(value, value["inputs"], value["environment"])
    if audited["status"] not in {"reported_passed", "reported_gap"}:
        raise ValueError(f"Collected evidence is incomplete: {audited}")


def report(repo: Path, changed: list[str] | None = None) -> dict:
    inputs, runtime = inventory(repo), environment(repo)
    tests, paths = collect(repo, ["ALL"]), metrics.production_files(repo)
    if not tests or not paths:
        raise ValueError("Nonempty test and production source inventories are required")
    # Validate format before costly execution; this probe is discarded, never evidence.
    validate_shape(
        {
            "version": 1,
            "inputs": inputs,
            "environment": runtime,
            "tests": tests,
            "faults": [],
            "files": [{"path": paths[0], "executing": [], "selected": []}],
        }
    )
    covered, outcome = metrics.coverage_by_test(repo)
    if not tests or outcome.get("returncode") != 0 or not covered:
        raise ValueError("A successful nonempty suite and per-test coverage data are required")
    rows, indices = [], {test: index for index, test in enumerate(tests)}
    for path in paths:
        targets, _ = metrics.select(repo, [path])
        if targets is None or path not in inputs:
            raise ValueError(f"Selector or fingerprint missing for {path}")
        selected = tests if targets == ["ALL"] else collect(repo, targets)
        executing = sorted(set().union(*covered.get(path, {}).values()))
        rows.append(
            {
                "path": path,
                "executing": references(executing, indices),
                "selected": references(selected, indices),
            }
        )
    measurement = measure_diff(repo, changed, tests) if changed else None
    if inputs != inventory(repo) or runtime != environment(repo):
        raise ValueError("Candidate inputs or environment changed during evidence collection")
    value = {
        "version": 1,
        "inputs": inputs,
        "environment": runtime,
        "tests": tests,
        "files": rows,
        "faults": [],
    }
    if measurement is not None:
        value["measurement"] = measurement
    validate_shape(value)
    return value


def write_report(repo: Path, output: Path, changed: list[str] | None = None) -> dict:
    repo, output = safe_path(repo), safe_path(output)
    if not repo.is_dir():
        raise ValueError("Candidate repository must be a directory")
    if output.is_relative_to(repo) and output != repo / REPORT_PATH:
        raise ValueError(f"In-repository output must be {REPORT_PATH}")
    canonical = output == repo / REPORT_PATH
    if output.exists() and not canonical:
        raise FileExistsError(f"Refusing to overwrite {output}")
    if output.exists() and not output.is_file():
        raise ValueError("Output must name a regular file")
    if any(
        Path(path).is_absolute() or ".." in Path(path).parts or not path for path in changed or []
    ):
        raise ValueError("Changed paths must be repository-relative")
    value = report(repo, changed)
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > MAX_BYTES:
        raise ValueError("CI evidence exceeds the 1 MB report limit")
    output.parent.mkdir(parents=True, exist_ok=True)
    safe_path(output)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent, prefix=".ci-evidence-", delete=False
        ) as f:
            temporary = Path(f.name)
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        safe_path(output)
        if canonical:
            os.replace(temporary, output)  # Keep the last valid report until publication.
        else:
            os.link(temporary, output, follow_symlinks=False)  # Exclusive publication.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help=f"Refresh {REPORT_PATH} in the candidate, or create a new external file",
    )
    parser.add_argument(
        "--changed",
        action="append",
        default=[],
        help="Supplied changed path; repeat for the complete change scope",
    )
    parser.add_argument(
        "--measure-diff",
        action="store_true",
        help="Also time full and selected tests without coverage",
    )
    args = parser.parse_args()
    if args.measure_diff != bool(args.changed):
        parser.error(
            "--measure-diff requires --changed paths, and --changed requires --measure-diff"
        )
    try:
        value = write_report(args.repo, args.output, args.changed)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Evidence collection failed: {error}\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "tests": len(value["tests"]),
                "files": len(value["files"]),
                "faults_sampled": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
