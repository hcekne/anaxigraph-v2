"""Explicit exploratory forks preserve a stopped campaign and every spent session."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

try:
    from .mutation_state import atomic
except ImportError:
    from mutation_state import atomic

PROTOCOL = "exploratory-continuation-v1"
EXCLUDED = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".uv-cache"}


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_stopped(session):
    if session.get("status") == "running" and not session.get("pid"):
        raise ValueError(
            "Running coding session has no saved process identity; inspect its process scope"
        )
    if session.get("status") == "running" and session.get("pid"):
        try:
            os.kill(session["pid"], 0)
        except ProcessLookupError:
            pass
        else:
            same = True
            if session.get("process_start") and session.get("boot_id"):
                stat = Path(f"/proc/{session['pid']}/stat").read_text().rsplit(")", 1)[1].split()
                same = (
                    stat[19] == session["process_start"]
                    and stat[0] != "Z"
                    and Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                    == session["boot_id"]
                )
            if same:
                raise ValueError(
                    "Recorded coding process is still running; refusing a second writer"
                )


def recovered_session(log: Path, driver) -> dict:
    """Reuse a spent call, including an interrupted call with unknown final cost."""
    session_path = log.with_suffix(".session.json")
    session = json.loads(session_path.read_text()) if session_path.exists() else {}
    ensure_stopped(session)
    raw = log.read_text() if log.exists() else ""
    summary = driver.summarize_events(raw)
    complete = session.get("status") in {"completed", "failed", "timeout"}
    elapsed = session.get("seconds") if complete else None
    usage_known = bool(summary.get("turns")) and (
        complete and session["status"] == "completed" or not session
    )
    status = session.get("status", "recovered-completed" if usage_known else "interrupted")
    return {
        **summary,
        "status": "interrupted" if status == "running" else status,
        "seconds": elapsed,
        "sessions": 1,
        "seconds_unknown_sessions": int(elapsed is None),
        "usage": summary.get("usage") if usage_known else None,
        "known_usage": summary.get("usage"),
        "usage_unknown_sessions": int(not usage_known),
        "contaminated": "feature_drift" in raw or "scenario/hidden" in raw,
        "recovered_session": {
            "transcript_sha256": fingerprint(log) if log.exists() else None,
            "session": session,
        },
    }


def run_or_recover(config, repo, log, prompt, treatment, driver):
    if log.exists() or log.with_suffix(".session.json").exists():
        return recovered_session(log, driver)
    return driver.run_agent(config, repo, log, prompt, treatment)


def source_records(source: Path) -> dict[str, str]:
    """Hash immutable session/history evidence, rather than rebuildable environments."""
    records = {}
    for directory, folders, files in os.walk(source):
        folders[:] = sorted(name for name in folders if name not in EXCLUDED)
        for name in sorted(files):
            path = Path(directory) / name
            value = "symlink:" + os.readlink(path) if path.is_symlink() else fingerprint(path)
            records[path.relative_to(source).as_posix()] = value
    return records


def prepare(config, driver) -> None:
    manifest = config.state / "continuation.json"
    source = getattr(config, "continue_from", None)
    if manifest.exists():
        value = json.loads(manifest.read_text())
        if source and Path(value["source_state"]) != source.resolve():
            raise ValueError("Continuation state belongs to another frozen campaign")
        if value["additional_ci_repairs"] != config.additional_ci_repairs:
            raise ValueError(
                "Continuation repair allowance is frozen; create another explicit fork"
            )
        if source_records(Path(value["source_state"])) != value["source_records"]:
            raise ValueError("Frozen source history changed after the continuation fork")
        config.continuation = value
        return
    if source is None:
        config.continuation = None
        return
    source = source.resolve(strict=True)
    if config.state == source or config.state.is_relative_to(source):
        raise ValueError("Continuation requires a separate new state outside its source")
    if config.state.exists():
        raise ValueError("Continuation destination must not already exist")
    runs = sorted(source.glob("run-*.json"))
    if not runs:
        raise ValueError("Continuation source has no recorded run provenance")
    previous = json.loads(runs[-1].read_text())
    for key in (
        "scenario_identity",
        "model",
        "effort",
        "agent",
        "review_steps",
        "review_kinds",
        "workflow_config_sha256",
        "ci_repair_attempts",
        "measurement_settings",
    ):
        if previous.get(key) != config.provenance.get(key):
            raise ValueError(f"Continuation must retain original {key}")
    records = source_records(source)
    value = {
        "protocol": PROTOCOL,
        "status": "exploratory",
        "source_state": str(source),
        "created": driver.now(),
        "additional_ci_repairs": config.additional_ci_repairs,
        "original_provenance": previous,
        "source_records": records,
        "note": "Original results and failed checkpoints remain authoritative; this extension is not the original preregistered comparison.",
    }
    audit_path = getattr(config, "continuation_audit", None)
    if audit_path:
        audit = json.loads(audit_path.read_text())
        if (
            Path(audit["frozen_state"]).resolve() != source
            or audit.get("continuation_override_category")
            != "audited-public-documentation-reference"
            or not audit.get("matches")
            or audit.get("scenario_hidden_hits")
        ):
            raise ValueError("Contamination audit must explicitly classify this frozen campaign")
        for match in audit["matches"]:
            path = Path(match["path"]).resolve(strict=True)
            if not path.is_relative_to(source) or fingerprint(path) != match["source_sha256"]:
                raise ValueError("Contamination audit does not match the frozen transcript")
        value["audited_contamination"] = {
            "audit_sha256": fingerprint(audit_path),
            "category": audit["continuation_override_category"],
            "scope": audit["scope"],
            "original_flag_preserved": True,
            "matches": [
                {key: match[key] for key in ("path", "line", "source_sha256")}
                for match in audit["matches"]
            ],
        }
    try:
        shutil.copytree(
            source, config.state, symlinks=True, ignore=shutil.ignore_patterns(*EXCLUDED)
        )
        for chain in sorted(config.state.glob("*-r*")):
            metadata = chain / "chain.json"
            if not metadata.exists():
                continue
            old = json.loads(metadata.read_text())
            driver.write_json(chain / "continuation-original-chain.json", old)
            driver.write_json(
                metadata,
                old
                | config.provenance
                | {
                    "continuation": {
                        "protocol": value["protocol"],
                        "additional_ci_repairs": value["additional_ci_repairs"],
                    }
                },
            )
        driver.write_json(manifest, value)
        if source_records(source) != records:
            raise ValueError("Frozen source changed during the continuation fork")
    except BaseException:
        shutil.rmtree(config.state, ignore_errors=True)
        raise
    config.continuation = value


def original_baseline(config, chain: Path, row: dict, driver) -> None:
    """Recreate the same original source tree at the fork's new checkout identity."""
    repo = chain / "repo"
    mapping = chain / f"continuation-baseline-{row['step']:02d}.json"
    current = driver.sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
    base = row["base_commit"]
    if current == base and row["commit"] != base:
        driver.sh(["git", "checkout", "--detach", row["commit"]], cwd=repo)
        current = row["commit"]
    if mapping.exists():
        row["baseline_id"] = json.loads(mapping.read_text())["baseline_id"]
        return
    tree = driver.sh(["git", "rev-parse", base + "^{tree}"], cwd=repo).stdout.strip()
    driver.sh(["git", "checkout", "--detach", base], cwd=repo)
    try:
        if driver.collector.inventory(repo) != row["baseline_inputs"]:
            raise ValueError("Fork baseline source inputs do not match the original request")
        scan = driver.runtime(config, repo, "scan")
        identity = scan.get("snapshot", {}).get("id")
        if not identity:
            raise ValueError("Cannot recover the original structural baseline in the fork")
        prepared = driver.runtime(
            config, repo, "prepare", f"Continue request {row['step']}", "--baseline-id", identity
        )
        if prepared.get("baseline_id") != identity:
            raise ValueError("Continuation did not pin the recovered original baseline")
        driver.write_json(
            mapping,
            {
                "base_commit": base,
                "base_tree": tree,
                "candidate_commit": current,
                "original_baseline_id": row.get("baseline_id"),
                "baseline_id": identity,
                "inputs_sha256": driver.scenarios.digest(row["baseline_inputs"]),
            },
        )
        row["baseline_id"] = identity
    finally:
        driver.sh(["git", "checkout", "--detach", current], cwd=repo)


def recover_legacy_mutant(chain: Path, step: int, agent: dict, driver) -> dict | None:
    """Only undo an exact evaluator transform after its paid candidate was committed."""
    if agent.get("status") not in {"completed", "recovered-completed"}:
        return None
    repo = chain / "repo"
    head = driver.sh(["git", "log", "-1", "--format=%H %s"], cwd=repo).stdout.strip()
    commit, _, subject = head.partition(" ")
    if not subject.startswith(f"Step {step}:"):
        return None
    safety = chain / "eval" / f"step-{step:02d}" / "safety"
    if any(safety.glob("repair-*.jsonl")):
        return None  # Later paid work needs its own transcript, never an inferred reset.
    changed = driver.sh(["git", "diff", "--name-only", "HEAD"], cwd=repo).stdout.splitlines()
    source = [path for path in changed if not driver.metrics.is_scratch(path)]
    if not source:
        return None
    if len(source) != 1 or not source[0].endswith(".py") or driver.metrics.is_test_path(source[0]):
        raise ValueError(
            "Completed candidate has unclassified post-commit source changes; preserve for audit"
        )
    relative = source[0]
    original_bytes = subprocess.run(
        ["git", "show", f"{commit}:{relative}"], cwd=repo, check=True, capture_output=True
    ).stdout
    original = original_bytes.decode("utf-8")
    file = repo / relative
    observed = file.read_text()
    matches = []
    for index, line in driver.metrics.candidate_mutants(original):
        mutated = driver.metrics.mutate(original, index)
        if mutated and mutated[0] == observed:
            matches.append({"index": index, "line": line, "kind": mutated[1][0]})
    if len(matches) != 1:
        raise ValueError(
            "Completed candidate changes are not one exact evaluator mutation; preserve for audit"
        )
    proof = {
        "classification": "exact-unrestored-evaluator-mutation",
        "paid_commit": commit,
        "path": relative,
        "mutation": matches[0],
        "original_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "mutant_sha256": fingerprint(file),
        "source_transcript_sha256": fingerprint(chain / "logs" / f"step-{step:02d}.jsonl"),
        "status": "classified-before-restoration",
    }
    backup = chain / f"legacy-mutant-step-{step:02d}.bin"
    atomic(backup, file.read_bytes())
    receipt = chain / f"legacy-mutant-step-{step:02d}.json"
    driver.write_json(receipt, proof)
    atomic(file, original_bytes, file.stat().st_mode & 0o777)
    proof["status"] = "restored-paid-candidate"
    driver.write_json(receipt, proof)
    return proof


def interrupted_row(config, chain: Path, step: int, driver) -> dict:
    repo = chain / "repo"
    prompt = chain / "logs" / f"step-{step:02d}.prompt.md"
    transcript = prompt.with_name(f"step-{step:02d}.jsonl")
    agent = recovered_session(transcript, driver)
    mutation = recover_legacy_mutant(chain, step, agent, driver)
    commits = driver.sh(["git", "log", "--format=%H %s"], cwd=repo).stdout.splitlines()
    bases = [
        line.split(" ", 1)[0] for line in commits if line.split(" ", 1)[1] == f"Request {step}"
    ]
    if not bases:
        raise ValueError("Interrupted request has no original Git baseline")
    base = bases[0]
    current = driver.commit(repo, f"Preserve interrupted candidate, request {step}")
    driver.sh(["git", "checkout", "--detach", base], cwd=repo)
    baseline_inputs = driver.collector.inventory(repo)
    driver.sh(["git", "checkout", "--detach", current], cwd=repo)
    row = {
        "step": step,
        "arm": chain.name.rsplit("-r", 1)[0],
        "replicate": int(chain.name.rsplit("-r", 1)[1]),
        "started": driver.now(),
        "base_commit": base,
        "commit": current,
        "baseline_inputs": baseline_inputs,
        "agent": agent,
        "continuation": {
            "recovered_interrupted_request": True,
            "original_session_seconds": None,
            "evaluator_mutation_recovery": mutation,
        },
    }
    preparation = chain / "eval" / f"step-{step:02d}" / "preparation.json"
    if preparation.exists():
        row["anaxigraph"] = json.loads(preparation.read_text())
        row["baseline_id"] = row["anaxigraph"].get("preparation", {}).get("baseline_id")
    return row


def resume(config, chain: Path, driver) -> None:
    """Resume only the fork; original rows, transcript files and failed attempts survive."""
    if not getattr(config, "continuation", None):
        return
    for directory in (chain / "logs", chain / "eval"):
        for session in directory.rglob("*.session.json"):
            ensure_stopped(json.loads(session.read_text()))
    driver.metrics.restore_mutation(chain / "repo")
    pending = sorted(chain.glob("pending-step-*.json"))
    if len(pending) > 1:
        raise ValueError("A chain cannot recover multiple pending requests at once")
    done = {row["step"] for row in driver.read_jsonl(chain / "steps.jsonl")}
    if pending:
        step = json.loads(pending[0].read_text())["step"]
        if step in done:
            rows = [row for row in driver.read_jsonl(chain / "steps.jsonl") if row["step"] == step]
            accepted = rows[0]
            archive = chain / f"continuation-original-step-{step:02d}.json"
            lineage = accepted.get("continuation", {})
            if (
                len(rows) != 1
                or accepted.get("ci_gate", {}).get("status") != "passed"
                or not archive.exists()
                or lineage.get("original_checkpoint_sha256") != fingerprint(archive)
            ):
                raise ValueError("Pending request conflicts with accepted history")
            driver.write_json(
                chain / f"continuation-retired-pending-{step:02d}.json",
                json.loads(pending[0].read_text()),
            )
            pending[0].unlink()
            return
    interrupted = next(
        (
            step
            for step in range(config.first_step, config.last_step + 1)
            if step not in done and (chain / "logs" / f"step-{step:02d}.prompt.md").exists()
        ),
        None,
    )
    if not pending and interrupted is None:
        return
    row = (
        json.loads(pending[0].read_text())
        if pending
        else interrupted_row(config, chain, interrupted, driver)
    )
    step, repo = row["step"], chain / "repo"
    archive = chain / f"continuation-original-step-{step:02d}.json"
    if not archive.exists():
        driver.write_json(archive, row)
    driver.sh(["uv", "sync", "--locked", "--quiet"], cwd=repo, timeout=600, env={"VIRTUAL_ENV": ""})
    treatment = driver.ARMS[row["arm"]]["anaxigraph"]
    old_baseline = row.get("baseline_id")
    if treatment:
        original_baseline(config, chain, row, driver)
    prompt = (chain / "logs" / f"step-{step:02d}.prompt.md").read_text()
    if old_baseline:
        prompt = prompt.replace(old_baseline, row["baseline_id"])
    prior = list(row.get("ci_attempts", []))
    validation_already_accounted = sum(a.get("anaxigraph_seconds", 0) for a in prior)
    safety = chain / "eval" / f"step-{step:02d}" / "safety"
    while (safety / f"attempt-{len(prior)}.json").exists():
        attempt = json.loads((safety / f"attempt-{len(prior)}.json").read_text())
        if attempt.get("number") != len(prior):
            raise ValueError("Saved repair attempts are not a contiguous history")
        prior.append(attempt)
    if prior:
        row["commit"] = prior[-1]["commit"]
    if not prior and row["agent"]["status"] == "interrupted":
        agent = run_or_recover(
            config,
            repo,
            chain / "logs" / f"continuation-step-{step:02d}.jsonl",
            prompt,
            treatment,
            driver,
        )
        row["agent"] = driver.aggregate_agents([{"agent": row["agent"]}, {"agent": agent}])
        row["commit"] = driver.commit(repo, f"Continue interrupted request {step}")
    if step >= driver.CI_FROM:
        result = driver.repair_ci(
            config,
            repo,
            safety,
            prompt,
            row["agent"],
            step,
            row["base_commit"],
            row["commit"],
            baseline_id=row.get("baseline_id"),
            baseline_inputs=row["baseline_inputs"],
            treatment=treatment,
            prior_attempts=deepcopy(prior),
        )
        row.update(result)
        if treatment:
            row.setdefault("anaxigraph", {"seconds": 0.0})["seconds"] += (
                result["ci_validation_seconds"] - validation_already_accounted
            )
    else:
        row.update(driver.evaluate(config, chain, repo, step, row["base_commit"], row["commit"]))
    row["continuation"] = {
        **row.get("continuation", {}),
        "protocol": PROTOCOL,
        "original_checkpoint": str(archive),
        "original_checkpoint_sha256": fingerprint(archive),
        "additional_ci_repairs": config.continuation["additional_ci_repairs"],
        "original_ci_gate": json.loads(archive.read_text()).get("ci_gate"),
    }
    row["finished"] = driver.now()
    path = chain / f"pending-step-{step:02d}.json"
    if row.get("ci_gate", {}).get("status", "passed") != "passed":
        driver.write_json(path, row)
        return
    driver.append_jsonl(chain / "steps.jsonl", row)
    path.unlink(missing_ok=True)
