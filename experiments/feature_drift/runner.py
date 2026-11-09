"""Run the feature-drift benchmark: one product, twenty requests, several arms in parallel.

Each chain (arm × replicate) copies the seed, then for every request:

1. saves the request under `docs/requests/` and commits it;
2. AnaxiGraph arms only: scans, refreshes semantic mapping and, at review steps, runs the
   configured broad reviews (Patterns already includes CI/CD);
3. runs one fresh Codex session (no memory of earlier sessions) with the request;
4. commits whatever the agent left and measures: hidden acceptance tests for every request so
   far, the agent's own suite, static shape, what the commit touched and CI selection quality.

After the last request, each probe request is applied to a separate copy of the final code by
the same model with no charter and no AnaxiGraph, measuring how easy the result is to change.

    uv run python experiments/feature_drift/runner.py --state ~/drift/pilot \\
        --arms bare,charter,anaxigraph --replicates 1 --steps 1-5 --no-probes

Live runs call paid models. Rerunning with the same `--state` resumes after completed steps.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from inspect import getfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collect_evidence as collector  # noqa: E402
import metrics  # noqa: E402

try:
    from experiments.feature_drift import scenarios
except ModuleNotFoundError:
    import scenarios

HERE = Path(__file__).resolve().parent
SCENARIO = HERE / "scenario"
SEED, HIDDEN = SCENARIO / "seed", SCENARIO / "hidden"
REQUESTS = sorted((SCENARIO / "requests").glob("*.md"))
PROBES = sorted((SCENARIO / "probes").glob("*.md"))
PRODUCT, CI_FROM = "Switchboard", 6
CHARTER = (HERE / "charter.md").read_text()
DEFAULT_RUNTIME = HERE.parent.parent / ".venv" / "bin" / "anaxigraph"

ARMS = {
    "bare": {"charter": False, "anaxigraph": False},
    "charter": {"charter": True, "anaxigraph": False},
    "anaxigraph": {"charter": True, "anaxigraph": True},
    "anaxigraph-only": {"charter": False, "anaxigraph": True},
}
DISABLED_FEATURES = ("memories", "apps", "plugins", "hooks", "multi_agent", "daemon_auto_start")
EXCLUDE = [".venv/", "__pycache__/", ".pytest_cache/", ".coverage", "data/", ".anaxigraph-v2/",
           "anaxigraph/record.json", "anaxigraph/config.json", ".ruff_cache/", ".uv-cache/", "pytest-of-*/"]  # fmt: skip

BASE_PROMPT = """You are the engineer maintaining this repository ({product}). A new product
request has arrived. It is saved at `{path}`; earlier requests are in `docs/requests/`.

{request}

Implement it completely in this repository, with tests. Everything earlier requests asked for
must keep working.

- There is no network access. The locked virtualenv in `.venv` is all there is; do not add
  dependencies. Run tests with `.venv/bin/python -m pytest`.
- Keep `run.json` working: the service is started from it.
- Do not commit; the coordinator commits your work. Independent CI-safety feedback may request
  a bounded repair in a fresh session. Complete the request and your checks now.

Your final response submits a candidate for the coordinator's external verification; it does not
certify completion. If AnaxiGraph requests fresh CI evidence, report that unqualified status and
hand off. The coordinator collects the evidence and accepts the task only after its safety gate.
Reply with a short summary of your changes and any outstanding validation."""

ANAXIGRAPH_PROMPT = """## AnaxiGraph

AnaxiGraph is connected as MCP tools. Its map of this repository was refreshed just before this
request (missing/stale production understanding every request; broad reviews at configured
checkpoints). Use it
as your architecture partner throughout:

1. Before designing, call `prepare_change` with the request and inspect the owners, related
   modules, saved reviews and checks it points to.
2. Before editing, call `assess_plan` with your planned file placement and resolve any decision
   it requires.
3. After implementing and running tests, call `scan`, then `validate_change` with your results.

Use `inspect`, `modules` and `reviews` whenever you need to know who depends on what. Do not
start model workflows yourself; the harness keeps the map current."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, value) -> None:
    atomic_text(path, json.dumps(value, indent=1, default=str))


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def append_jsonl(path: Path, value) -> None:
    previous = path.read_text() if path.exists() else ""
    atomic_text(path, previous + json.dumps(value, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sh(argv, cwd=None, timeout=None, env=None, check=True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, **(env or {})},
    )  # fmt: skip
    if check and result.returncode:
        raise RuntimeError(f"{argv[:3]} failed: {result.stderr[-1500:]}")
    return result


def commit(repo: Path, message: str) -> str:
    metrics.restore_mutation(repo)
    sh(["git", "-C", str(repo), "add", "-A"])
    sh(["git", "-C", str(repo), "commit", "--allow-empty", "-q", "-m", message])
    return sh(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip()


def prompt_for(arm: dict, request: Path, saved_as: str) -> str:
    text = BASE_PROMPT.format(product=PRODUCT, path=saved_as, request=request.read_text().strip())
    if arm["charter"]:
        text += "\n\n" + CHARTER.strip()
    if arm["anaxigraph"]:
        text += "\n\n" + ANAXIGRAPH_PROMPT
    return text


# -- agent ----------------------------------------------------------------------------------


def codex_argv(config, repo: Path, with_anaxigraph: bool) -> list[str]:
    argv = ["codex"]
    for feature in DISABLED_FEATURES:
        argv += ["--disable", feature]
    settings = {
        "model_reasoning_effort": json.dumps(config.effort),
        "approval_policy": '"never"',
        "project_doc_max_bytes": "0",
        "mcp_servers": "{}",
        "sandbox_workspace_write.network_access": "false",
    }
    if with_anaxigraph:
        settings.update(
            {
                "mcp_servers.anaxigraph.command": json.dumps(str(config.runtime)),
                "mcp_servers.anaxigraph.args": json.dumps(["--repo", str(repo), "mcp"]),
                "mcp_servers.anaxigraph.required": "true",
                "mcp_servers.anaxigraph.default_tools_approval_mode": '"approve"',
                "mcp_servers.anaxigraph.tool_timeout_sec": "3600",
            }
        )
    for key, value in settings.items():
        argv += ["--config", f"{key}={value}"]
    return argv + [
        "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check",
        "--json", "--model", config.model, "--sandbox", "workspace-write", "--color", "never",
        "--cd", str(repo), "-",
    ]  # fmt: skip


def summarize_events(raw: str) -> dict:
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    counts: dict[str, int] = {}
    mcp_tools: dict[str, int] = {}
    mcp_failures, final, turns = 0, "", 0
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.completed":
            turns += 1
            for key in usage:
                usage[key] += int((event.get("usage") or {}).get(key) or 0)
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type"):
            counts[item["type"]] = counts.get(item["type"], 0) + 1
            if item["type"] == "mcp_tool_call":
                name = f"{item.get('server')}.{item.get('tool')}"
                mcp_tools[name] = mcp_tools.get(name, 0) + 1
                reply = item.get("result")
                if item.get("error") or (isinstance(reply, dict) and reply.get("isError")):
                    mcp_failures += 1
            if item["type"] == "agent_message":
                final = item.get("text", "")
    return {
        "usage": usage,
        "turns": turns,
        "items": counts,
        "mcp_tools": mcp_tools,
        "mcp_failures": mcp_failures,
        "final_message": final[-2000:],
    }


def run_agent(config, repo: Path, log: Path, prompt: str, with_anaxigraph: bool) -> dict:
    if log.exists() or log.with_suffix(".prompt.md").exists():
        raise FileExistsError(
            f"Agent session already exists; retain it and use a new session: {log}"
        )
    log.parent.mkdir(parents=True, exist_ok=True)
    (log.with_suffix(".prompt.md")).write_text(prompt)
    started = time.monotonic()
    if config.agent == "noop":
        return {"status": "noop", "seconds": 0.0}
    if config.agent.startswith("ref:"):
        return checkout_reference(Path(config.agent[4:]), log.stem, repo, started)
    if config.agent.startswith("copy:"):
        source = Path(config.agent[5:])
        for path in source.rglob("*"):
            rel = path.relative_to(source)
            if path.is_file() and not any(
                p in {".venv", ".git", "__pycache__", ".pytest_cache", "data"} for p in rel.parts
            ):
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, repo / rel)
        return {"status": "copied", "seconds": round(time.monotonic() - started, 1)}
    env = {"UV_OFFLINE": "1", "UV_NO_SYNC": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    session = {"status": "running", "started": now(), "log": str(log)}
    write_json(log.with_suffix(".session.json"), session)
    with log.open("w") as stdout, log.with_suffix(".stderr.txt").open("w") as stderr:
        process = subprocess.Popen(
            codex_argv(config, repo, with_anaxigraph),
            stdin=subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
            text=True,
            env={**os.environ, **env},
            start_new_session=True,
        )
        session["pid"] = process.pid
        try:
            session["process_start"] = (
                Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
            )
            session["boot_id"] = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            session["process_start"] = None
        write_json(log.with_suffix(".session.json"), session)
        try:
            process.communicate(prompt, timeout=config.agent_timeout)
            status = "completed" if process.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            status = "timeout"
            stderr.write("\nAgent timed out; streamed events retained.\n")
    session.update(status=status, finished=now(), seconds=round(time.monotonic() - started, 1))
    write_json(log.with_suffix(".session.json"), session)
    raw = log.read_text()
    summary = summarize_events(raw)
    return {
        "status": status,
        "seconds": session["seconds"],
        "usage_unknown_sessions": int(status != "completed" or summary["turns"] == 0),
        # The benchmark lives on the same machine; flag transcripts that touched it.
        "contaminated": "feature_drift" in raw or "scenario/hidden" in raw,
        **summary,
        "known_usage": summary["usage"],
        "usage": summary["usage"] if status == "completed" and summary["turns"] else None,
    }


def checkout_reference(source: Path, label: str, repo: Path, started: float) -> dict:
    """Replace the candidate tree with a reference repository's `step-NN` tag (harness tests)."""
    if not label.startswith("step-"):
        return {"status": "noop", "seconds": 0.0}
    keep = {".git", ".venv", "docs"}
    for path in repo.iterdir():
        if path.name not in keep:
            shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
    tar = subprocess.run(
        ["git", "-C", str(source), "archive", label], capture_output=True, check=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(repo)], input=tar, check=True)
    return {"status": "reference", "seconds": round(time.monotonic() - started, 1)}


def configure_track(track: dict) -> None:
    """Point the module-level scenario inputs at the selected executable track."""
    global SEED, HIDDEN, REQUESTS, PROBES, PRODUCT, CI_FROM
    paths = scenarios.layout(track)
    SEED, HIDDEN = paths["seed"], paths["hidden"]
    REQUESTS, PROBES = paths["requests"], paths["probes"]
    PRODUCT, CI_FROM = paths["product"], paths["ci_from_step"]


# -- AnaxiGraph refresh -----------------------------------------------------------------------


def runtime(config, repo: Path, *args: str, timeout: float = 7200) -> dict:
    result = sh([str(config.runtime), "--repo", str(repo), *args], timeout=timeout, check=False)
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        payload = {"raw": result.stdout[-500:]}
    if result.returncode:
        payload = {"error": result.stderr[-1500:], **(payload if isinstance(payload, dict) else {})}
    return payload


def workflow_summary(status: dict) -> dict:
    item = (
        status.get("items", [status])[0]
        if isinstance(status, dict) and "items" in status
        else status
    )
    if not isinstance(item, dict):
        return {"status": "unknown"}
    return {
        "status": item.get("status"),
        "error": item.get("error"),
        "model_calls": sum(s.get("model_calls", 0) for s in item.get("stage_timings") or []),
        "failed_batches": item.get("failed_batches"),
        "reused_batches": item.get("reused_batches"),
        "semantic_refresh": item.get("semantic_refresh"),
        "stage_timings": item.get("stage_timings"),
    }


def refresh_anaxigraph(config, repo: Path, step: int) -> dict:
    started = time.monotonic()
    extra = ["--config", str(config.workflow_config)] if config.workflow_config else []
    report = {"scan": bool(runtime(config, repo, "scan").get("snapshot"))}
    kinds = ["mapping"] + (list(config.review_kinds) if step in config.review_steps else [])
    for kind in kinds:
        report[kind] = workflow_summary(runtime(config, repo, "run", kind, *extra))
    report["seconds"] = round(time.monotonic() - started, 1)
    return report


# -- evaluation -----------------------------------------------------------------------------


def junit(path: Path) -> dict[str, str]:
    outcomes = {}
    if not path.exists():
        return outcomes
    for case in ET.parse(path).getroot().iter("testcase"):
        name = f"{case.get('classname')}::{case.get('name')}"
        if case.find("failure") is not None or case.find("error") is not None:
            outcomes[name] = "failed"
        elif case.find("skipped") is not None:
            outcomes[name] = "skipped"
        else:
            outcomes[name] = "passed"
    return outcomes


def hidden_tests(repo: Path, files: list[str], era: int, out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = sh(
        [sys.executable, "-m", "pytest", "-c", "pytest.ini", *files, "-q", f"--junitxml={out}"],
        cwd=HIDDEN,
        timeout=3600,
        env={"DRIFT_CANDIDATE": str(repo), "DRIFT_STEP": str(era)},
        check=False,
    )
    outcomes = junit(out)
    by_file: dict[str, dict] = {}
    for name, outcome in outcomes.items():
        module = name.split("::")[0].rsplit(".", 1)[-1]
        row = by_file.setdefault(module, {"passed": 0, "total": 0})
        row["total"] += 1
        row["passed"] += outcome == "passed"
    return {
        "seconds": round(time.monotonic() - started, 1),
        "by_file": by_file,
        "failed": sorted(n for n, o in outcomes.items() if o == "failed"),
        "passed": sorted(n for n, o in outcomes.items() if o == "passed"),
        "collection_error": result.returncode not in (0, 1),
        "returncode": result.returncode,
        "tail": result.stdout[-1500:] if result.returncode not in (0, 1) else "",
    }


def own_suite(repo: Path, out: Path) -> dict:
    started = time.monotonic()
    result = metrics.run(
        [*metrics.pytest_argv(repo), f"--junitxml={out}", "-o", "addopts="],
        repo,
        1800,
        metrics.candidate_env(repo),
    )
    outcomes = junit(out)
    return {
        "returncode": result.returncode,
        "seconds": round(time.monotonic() - started, 1),
        "tests": len(outcomes),
        "passed": sum(o == "passed" for o in outcomes.values()),
    }


def evaluate(config, chain: Path, repo: Path, step: int, base: str, head: str) -> dict:
    files = scenarios.acceptance_files(config.scenario, step)
    out = chain / "eval" / f"step-{step:02d}"
    row = {
        "hidden": hidden_tests(repo, files, step, out / "hidden.xml"),
        "own_suite": own_suite(repo, out / "own.xml"),
        "static": metrics.static_metrics(repo),
        "diff": metrics.diff_stats(repo, base, head),
    }
    if step >= CI_FROM and (step - CI_FROM) % config.ci_every == 0 or step == config.last_step:
        row["ci"] = metrics.ci_quality(repo, seed=step, mutants=config.mutants)
        write_json(out / "ci.json", row["ci"])
        row["ci"] = {k: v for k, v in row["ci"].items() if k != "per_file"}
    return row


def ci_gate(collected: dict) -> dict:
    """The same conservative external qualification is required in every coding arm."""
    quality, report = collected["ci"], collected.get("report")
    failures, incomplete = [], []
    if report is None:
        incomplete.append(collected.get("error") or "Missing fresh external CI evidence")
    else:
        audited = collector.audit_selection(report, report["inputs"], report["environment"])
        if audited["status"] in {"reported_gap", "reported_fault_miss"}:
            failures.append("Observed consumers or reference-detected faults omitted")
        elif audited["status"] != "reported_passed":
            incomplete.append("Fresh complete consumer evidence required")
    changes = quality.get("change_selection", {})
    if changes.get("status") == "failed":
        failures.append("Actual multi-file change omits observed obligations")
    elif changes.get("status") != "passed":
        incomplete.append("Actual multi-file selection is unverified")
    mutations = quality.get("mutants", {})
    if mutations.get("missed_by_selection"):
        failures.append("Selected checks missed a reference-detected fault")
    if not mutations.get("caught_by_suite") or mutations.get("unverified"):
        incomplete.append("Positive fully verified caught-mutant evidence required")
    return {
        "status": "failed" if failures else "incomplete" if incomplete else "passed",
        "failures": failures,
        "incomplete": incomplete,
    }


def aggregate_agents(attempts: list[dict]) -> dict:
    agents = [attempt["agent"] for attempt in attempts]
    result = {
        **agents[-1],
        "seconds": sum(agent.get("seconds", 0) for agent in agents)
        if all(agent.get("seconds") is not None for agent in agents)
        else None,
        "known_seconds": sum(
            agent.get("known_seconds", agent.get("seconds") or 0) for agent in agents
        ),
        "seconds_unknown_sessions": sum(
            agent.get("seconds_unknown_sessions", int(agent.get("seconds") is None))
            for agent in agents
        ),
        "sessions": sum(agent.get("sessions", 1) for agent in agents),
        "usage_unknown_sessions": sum(
            agent.get("usage_unknown_sessions", int(not agent.get("usage"))) for agent in agents
        ),
    }
    result["usage"] = {
        key: sum(agent["usage"][key] for agent in agents)
        if all(
            type((agent.get("usage") or {}).get(key)) is int
            and not agent.get("usage_unknown_sessions", 0)
            for agent in agents
        )
        else None
        for key in ("input_tokens", "cached_input_tokens", "output_tokens")
    }
    result["known_usage"] = {
        key: sum(
            (agent.get("known_usage") or agent.get("usage") or {}).get(key) or 0 for agent in agents
        )
        for key in ("input_tokens", "cached_input_tokens", "output_tokens")
    }
    for field in ("items", "mcp_tools"):
        names = {name for agent in agents for name in (agent.get(field) or {})}
        result[field] = {
            name: sum((agent.get(field) or {}).get(name, 0) for agent in agents) for name in names
        }
    result["mcp_failures"] = sum(agent.get("mcp_failures", 0) for agent in agents)
    result["contaminated"] = any(agent.get("contaminated", False) for agent in agents)
    return result


def fresh_validation(config, repo, out, baseline_id, collected):
    """Only supply observed receipts; the Application never executes candidate checks."""
    scan = runtime(config, repo, "scan")
    snapshot = (scan.get("snapshot") or {}).get("id")
    report = collected.get("report")
    if not snapshot or not baseline_id or report is None:
        return {
            "completion_allowed": False,
            "validation_status": "incomplete",
            "error": "Missing original baseline, current scan or fresh evidence",
        }
    context, receipts = out / "runtime-context.json", out / "receipts.json"
    write_json(context, report["environment"])
    write_json(
        receipts,
        [
            {
                "command": report["suite"].get(
                    "command", ".venv/bin/python -m pytest --cov --cov-context=test"
                ),
                "exit_code": report["suite"]["returncode"],
                "snapshot_id": snapshot,
            }
        ],
    )
    result = runtime(
        config,
        repo,
        "validate",
        "--baseline-id",
        baseline_id,
        "--runtime-context",
        str(context),
        "--require-ci-evidence",
        "--receipts",
        str(receipts),
    )
    write_json(out / "validation.json", result)
    return result


def safety_feedback(
    config,
    repo,
    out,
    step,
    base,
    head,
    *,
    baseline_id=None,
    baseline_inputs=None,
    treatment=False,
    acceptance_files=None,
    allowed_failures=None,
):
    """Collect once, publish fresh evidence, then expose the same measured gap to each arm.

    Hidden failures already present before a repair (`allowed_failures`; None means every
    failure of the original attempt) are scored, not gated: repair agents never see the
    hidden checks, so the gate only refuses repairs that add new behavioral failures."""
    started = time.monotonic()
    out.mkdir(parents=True, exist_ok=True)
    diff = metrics.diff_stats(repo, base, head)
    current = collector.inventory(repo)
    changed = (
        sorted(
            path
            for path in set(baseline_inputs) | set(current)
            if baseline_inputs.get(path) != current.get(path)
        )
        if baseline_inputs is not None
        else diff["changed_files"]
    )
    collected = collector.quality_report(
        repo,
        repo / collector.REPORT_PATH,
        seed=step,
        mutants=config.mutants,
        changed=changed,
    )
    quality = collected["ci"]
    write_json(out / "ci.json", quality)
    if collected.get("report") is not None:
        write_json(out / "ci-evidence.json", collected["report"])
    gate = ci_gate(collected)
    files = acceptance_files or scenarios.acceptance_files(config.scenario, step)
    hidden = hidden_tests(repo, files, step, out / "hidden.xml")
    if collected.get("report") is not None and (
        collected["report"]["inputs"] != collector.inventory(repo)
        or collected["report"]["environment"] != collector.environment(repo)
    ):
        collected["report"] = None
        collected["error"] = (
            "Candidate inputs or environment changed after verification; evidence is stale"
        )
        gate = {
            **gate,
            "status": "incomplete",
            "incomplete": gate["incomplete"] + [collected["error"]],
        }
    expected_hidden = {Path(name).stem for name in files}
    failed = set(hidden.get("failed", []))
    if (
        hidden.get("collection_error")
        or set(hidden.get("by_file", {})) != expected_hidden
        or any(not row.get("total") for row in hidden.get("by_file", {}).values())
        or (allowed_failures is not None and failed - set(allowed_failures))
    ):
        gate = {
            **gate,
            "status": "failed",
            "failures": gate["failures"]
            + [
                "Cumulative behavioral acceptance is incomplete or regressed; preserve the public requirements"
            ],
        }
    suite = quality.get("coverage_run") or {}
    own = {
        key: suite.get(key, 0 if key in {"tests", "passed"} else None)
        for key in ("returncode", "tests", "passed", "seconds")
    }
    evaluation = {
        "hidden": hidden,
        "own_suite": own,
        "static": metrics.static_metrics(repo),
        "diff": diff,
        "ci": {key: value for key, value in quality.items() if key != "per_file"},
    }
    verification_seconds = time.monotonic() - started
    validation, tool_seconds = None, 0.0
    if treatment:
        started = time.monotonic()
        validation = fresh_validation(config, repo, out, baseline_id, collected)
        tool_seconds = time.monotonic() - started
        if validation.get("completion_allowed") is not True:
            gate = {
                **gate,
                "status": "incomplete" if gate["status"] == "passed" else gate["status"],
                "incomplete": gate["incomplete"]
                + ["Fresh AnaxiGraph validation did not qualify completion"],
            }
    feedback = {
        "gate": gate,
        "collector_error": collected.get("error"),
        "consumer_evidence": collector.audit_selection(
            collected["report"], collected["report"]["inputs"], collected["report"]["environment"]
        )
        if collected.get("report")
        else None,
        "actual_change": quality.get("change_selection"),
        "failing_own_tests": (quality.get("coverage_run") or {}).get("failed_tests", []),
        "own_failure_details": (quality.get("coverage_run") or {}).get("failure_details", []),
        "own_resource_usage": (quality.get("coverage_run") or {}).get("resource_usage"),
        "unstable_test_ids": quality.get("unstable_test_ids"),
        "sampled_faults": quality.get("mutants"),
        "validation": validation,
    }
    write_json(out / "feedback.json", feedback)
    return {
        "evaluation": evaluation,
        "gate": gate,
        "feedback": feedback,
        "verification_seconds": verification_seconds,
        "anaxigraph_seconds": tool_seconds,
        "test_inventory": collected.get("test_inventory", []),
    }


def retained(test: str, inventory: set[str]) -> bool:
    """An id still runs when only its parametrisation became stable or unstable."""
    function = test.split("[", 1)[0]
    return function in inventory or any(other.startswith(function + "[") for other in inventory)


def repair_candidate(config, repo, log, prompt, step, number, feedback, baseline_id, treatment):
    compact = {
        **feedback,
        "actual_change": {
            key: value
            for key, value in (feedback.get("actual_change") or {}).items()
            if key not in {"selected_tests", "required_tests"}
        },
    }
    actual = compact["actual_change"]
    omitted = actual.get("omitted_tests", [])
    actual["omitted_tests"] = omitted[:20]
    actual["omitted_tests_total"] = len(omitted)
    if compact.get("validation"):
        compact["validation"] = {
            key: compact["validation"].get(key)
            for key in (
                "baseline_id",
                "validation_status",
                "completion_allowed",
                "ci_selection",
                "checks",
                "error",
            )
        }
    instruction = (
        "\n\nIndependent CI safety verification requires repair. Preserve all original requirements "
        "and the original change baseline. Repair the selector so all observed consumer obligations "
        "are retained for single-file AND multi-file changes, and make every listed failing_own_tests "
        "test pass; you may add new test files. Existing test and conftest files are frozen: do "
        "not edit, move, rename or delete them, except that files containing failing_own_tests may "
        "be corrected. Every existing test ID must still run; a repair that breaks this fails "
        "verification. Use focused "
        "contracts when justified; unknown/shared inputs need conservative fallback. Do not fake "
        "receipts, edit the external evaluator, or claim safety from an empty selection. "
        "The coordinator will rerun independent verification on your changed source.\n"
        + json.dumps(compact, default=str)
    )
    if treatment:
        instruction += (
            f"\nOriginal baseline_id={baseline_id}. Inspect fresh anaxigraph/ci-evidence.json, "
            "scan, then validate_change with this explicit original baseline. Do not prepare "
            "a new baseline or rerun model workflows. Fix deterministic blocked/incomplete findings."
        )
    if getattr(config, "continuation", None):
        import continuation

        agent = continuation.run_or_recover(
            config,
            repo,
            log / f"repair-{number}.jsonl",
            prompt + instruction,
            treatment,
            sys.modules[__name__],
        )
    else:
        agent = run_agent(
            config, repo, log / f"repair-{number}.jsonl", prompt + instruction, treatment
        )
    head = commit(repo, f"CI safety repair {number}, request {step}")
    return agent, head


def repaired_result(attempts, head):
    final = attempts[-1]
    return {
        **final["evaluation"],
        "commit": head,
        "agent": aggregate_agents(attempts),
        "ci_attempts": attempts,
        "ci_gate": final["gate"],
        "initial_ci_gate": attempts[0]["gate"],
        "verification_seconds": sum(attempt["verification_seconds"] for attempt in attempts),
        "ci_validation_seconds": sum(attempt["anaxigraph_seconds"] for attempt in attempts),
    }


def repair_ci(
    config,
    repo,
    log,
    prompt,
    initial_agent,
    step,
    base,
    head,
    *,
    baseline_id=None,
    baseline_inputs=None,
    treatment=False,
    acceptance_files=None,
    prior_attempts=None,
):
    """Bounded fresh sessions; retain original baseline, every attempt and all resource use."""
    attempts = list(prior_attempts or [])
    if attempts and attempts[-1]["gate"]["status"] == "passed":
        return repaired_result(attempts, attempts[-1]["commit"])
    agent = initial_agent
    obligations = log / "test-obligations.json"
    frozen_tests = (
        json.loads(obligations.read_text())
        if attempts and obligations.exists()
        else {
            path: fingerprint
            for path, fingerprint in collector.inventory(repo).items()
            if metrics.is_test_path(path)
        }
    )
    saved_inventory = log / "test-inventory.json"
    retained_ids = (
        set(json.loads(saved_inventory.read_text()))
        if attempts and saved_inventory.exists()
        else set()
    )
    if attempts and not retained_ids:
        retained_ids = set(metrics.collect(repo, ["ALL"]) or [])
        if not retained_ids:
            raise ValueError("Cannot continue repairs without the retained test inventory")
    extension = (getattr(config, "continuation", None) or {}).get("additional_ci_repairs", 0)
    limit = config.ci_repair_attempts + extension
    if attempts and len(attempts) <= limit:
        agent, head = repair_candidate(
            config,
            repo,
            log,
            prompt,
            step,
            len(attempts),
            attempts[-1]["feedback"],
            baseline_id,
            treatment,
        )
    for number in range(len(attempts), limit + 1):
        checked = safety_feedback(
            config,
            repo,
            log / f"attempt-{number}",
            step,
            base,
            head,
            baseline_id=baseline_id,
            baseline_inputs=baseline_inputs,
            treatment=treatment,
            acceptance_files=acceptance_files,
            allowed_failures=attempts[-1].get("evaluation", {}).get("hidden", {}).get("failed", [])
            if attempts
            else None,
        )
        inventory = set(checked.pop("test_inventory", []))
        # Tests failing in any attempt (for example ones asserting the old selection) may be
        # corrected, never removed; their files leave the frozen set.
        run = checked.get("evaluation", {}).get("ci", {}).get("coverage_run") or {}
        failed_files = set(run.get("failed_files", []))
        if not attempts:
            # Freeze modules holding collected tests and conftest files, not selector code or
            # data that merely sits in a test folder.
            modules = {test.split("::")[0] for test in inventory}
            frozen_tests = {
                path: fingerprint
                for path, fingerprint in frozen_tests.items()
                if Path(path).name == "conftest.py"
                or path in modules
                or (not modules and path.endswith(".py"))
            }
        frozen_tests = {p: f for p, f in frozen_tests.items() if p not in failed_files}
        write_json(log / "test-obligations.json", frozen_tests)
        if not retained_ids:
            retained_ids = inventory
        write_json(saved_inventory, sorted(retained_ids))
        current = collector.inventory(repo)
        changed_tests = [
            path for path, fingerprint in frozen_tests.items() if current.get(path) != fingerprint
        ]
        missing_ids = sorted(
            test for test in retained_ids - inventory if not retained(test, inventory)
        )
        if changed_tests or missing_ids:
            checked["gate"] = {
                **checked["gate"],
                "status": "failed",
                "failures": checked["gate"].get("failures", [])
                + [
                    "CI-only repair must retain existing test/conftest bytes and execute original test IDs"
                ],
            }
            checked["feedback"]["gate"] = checked["gate"]
            checked["feedback"]["changed_verification_paths"] = changed_tests[:20]
            checked["feedback"]["missing_test_ids"] = missing_ids[:20]
        checked["feedback"]["gate"] = checked["gate"]
        write_json(log / f"attempt-{number}" / "final-feedback.json", checked["feedback"])
        attempts.append({"number": number, "agent": agent, "commit": head, **checked})
        write_json(log / f"attempt-{number}.json", attempts[-1])
        if checked["gate"]["status"] == "passed" or number == limit or config.agent == "noop":
            break
        agent, head = repair_candidate(
            config, repo, log, prompt, step, number + 1, checked["feedback"], baseline_id, treatment
        )
    return repaired_result(attempts, head)


# -- chains ---------------------------------------------------------------------------------


def prepare_repo(repo: Path, source: Path) -> None:
    shutil.copytree(source, repo, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".git"))
    sh(["git", "init", "-q", "-b", "main", str(repo)])
    sh(["git", "-C", str(repo), "config", "user.name", "drift-harness"])
    sh(["git", "-C", str(repo), "config", "user.email", "drift-harness@example.invalid"])
    (repo / ".git" / "info" / "exclude").write_text("\n".join(EXCLUDE) + "\n")
    sh(["uv", "sync", "--locked", "--quiet"], cwd=repo, timeout=600, env={"VIRTUAL_ENV": ""})


def run_chain(config, arm_name: str, replicate: int) -> dict:
    arm = ARMS[arm_name]
    chain = config.state / f"{arm_name}-r{replicate}"
    repo, results = chain / "repo", chain / "steps.jsonl"
    validate_resume(config, chain)
    if getattr(config, "continuation", None) and repo.exists():
        import continuation

        continuation.resume(config, chain, sys.modules[__name__])
    pending = sorted(chain.glob("pending-step-*.json"))
    if pending:
        return {
            "chain": chain.name,
            "status": "blocked",
            "pending": [str(path) for path in pending],
        }
    if not repo.exists():
        prepare_repo(repo, SEED)
        commit(repo, "Seed")
        write_json(chain / "chain.json", {"arm": arm_name, **arm, "replicate": replicate,
                                          "scenario": config.scenario,
                                          "continuation": {key: config.continuation[key] for key in ("protocol", "additional_ci_repairs")} if getattr(config, "continuation", None) else None,
                                          "started": now(), **config.provenance})  # fmt: skip
    done = {row["step"] for row in read_jsonl(results)}
    for request in REQUESTS:
        step = int(request.name[:2])
        if step < config.first_step or step > config.last_step or step in done:
            continue
        if (chain / "logs" / f"step-{step:02d}.prompt.md").exists():
            return {
                "chain": chain.name,
                "status": "blocked",
                "step": step,
                "reason": "Interrupted request retained; refusing to overwrite original sessions or reset the repair budget",
            }
        saved_as = f"docs/requests/{request.name}"
        (repo / "docs" / "requests").mkdir(parents=True, exist_ok=True)
        shutil.copy2(request, repo / saved_as)
        base = commit(repo, f"Request {step}")
        row = {"step": step, "arm": arm_name, "replicate": replicate, "started": now()}
        row["baseline_inputs"] = collector.inventory(repo)
        inline = next(
            (probe for probe in config.scenario["inline_probes"] if probe["step"] == step), None
        )
        if inline:
            row["probe_id"] = inline["id"]
        if arm["anaxigraph"] and config.agent not in ("noop",):
            row["anaxigraph"] = refresh_anaxigraph(config, repo, step)
        if arm["anaxigraph"]:
            started = time.monotonic()
            scan = runtime(config, repo, "scan")
            snapshot = (scan.get("snapshot") or {}).get("id")
            prepared = (
                runtime(
                    config, repo, "prepare", request.read_text()[:4000], "--baseline-id", snapshot
                )
                if snapshot
                else {}
            )
            row["baseline_id"] = prepared.get("baseline_id")
            row.setdefault("anaxigraph", {"seconds": 0.0})["preparation"] = prepared
            row["anaxigraph"]["seconds"] += time.monotonic() - started
            write_json(chain / "eval" / f"step-{step:02d}" / "preparation.json", row["anaxigraph"])
            if not row["baseline_id"]:
                raise RuntimeError("Cannot start coding without the original pinned baseline")
        prompt = prompt_for(arm, request, saved_as)
        if row.get("baseline_id"):
            prompt += f"\nOriginal pinned baseline_id={row['baseline_id']}; preserve it explicitly through validation."
        row["agent"] = run_agent(
            config, repo, chain / "logs" / f"step-{step:02d}.jsonl",
            prompt, arm["anaxigraph"],
        )  # fmt: skip
        head = commit(repo, f"Step {step}: {request.stem}")
        row["commit"] = head
        row["base_commit"] = base
        if step >= CI_FROM:
            row.update(
                repair_ci(
                    config,
                    repo,
                    chain / "eval" / f"step-{step:02d}" / "safety",
                    prompt,
                    row["agent"],
                    step,
                    base,
                    head,
                    baseline_id=row.get("baseline_id"),
                    baseline_inputs=row["baseline_inputs"],
                    treatment=arm["anaxigraph"],
                )
            )
            if arm["anaxigraph"]:
                row["anaxigraph"]["seconds"] += row["ci_validation_seconds"]
        else:
            started = time.monotonic()
            row.update(evaluate(config, chain, repo, step, base, head))
            row["verification_seconds"] = time.monotonic() - started
        row["finished"] = now()
        if row.get("ci_gate", {}).get("status", "passed") != "passed":
            write_json(chain / f"pending-step-{step:02d}.json", row)
            return {
                "chain": chain.name,
                "status": "blocked",
                "step": step,
                "reason": "CI safety remains unqualified after bounded repair; no later request started",
            }
        append_jsonl(results, row)
        hidden = row["hidden"]["by_file"]
        new = hidden.get(f"step_{step:02d}", {})
        total = sum(v["total"] for v in hidden.values())
        passed = sum(v["passed"] for v in hidden.values())
        print(
            f"[{arm_name}-r{replicate}] step {step}: new {new.get('passed', 0)}/{new.get('total', 0)}"
            f" cumulative {passed}/{total} agent={row['agent']['status']}"
            f" {row['agent']['seconds']}s",
            flush=True,
        )
    if config.probes and config.last_step == config.scenario["steps"][-1]:
        run_probes(config, chain, arm_name)
    return {"chain": chain.name, "status": "completed", "steps": len(read_jsonl(results))}


def selection_size(repo: Path, changed: list[str]) -> tuple[int | None, int]:
    """Tests the candidate's selector picks for a change, and the suite size."""
    targets, _ = metrics.select(repo, changed)
    total = len(metrics.collect(repo, ["ALL"]) or [])
    if targets is None:
        return None, total
    if targets == ["ALL"]:
        return total, total
    return len(metrics.collect(repo, targets) or []), total


def hidden_subset(value: dict, files: list[str]) -> dict:
    stems = {Path(name).stem for name in files}
    return {
        **value,
        "by_file": {key: row for key, row in value["by_file"].items() if key in stems},
        **{
            key: [
                name
                for name in value.get(key, [])
                if name.split("::")[0].rsplit(".", 1)[-1] in stems
            ]
            for key in ("passed", "failed")
        },
    }


def run_probes(config, chain: Path, arm_name: str) -> None:
    results = chain / "probes.jsonl"
    done = {row["probe"] for row in read_jsonl(results)}
    for number, request in enumerate(PROBES, 1):
        pending = chain / f"pending-probe-{request.stem}.json"
        if pending.exists():
            raise RuntimeError(
                f"Probe {request.stem} remains blocked; refusing additional automatic sessions"
            )
        if request.stem in done:
            continue
        if (chain / "logs" / f"probe-{request.stem}.prompt.md").exists():
            raise RuntimeError(
                f"Interrupted probe {request.stem} retained; no automatic session replay"
            )
        repo = chain / "probes" / request.stem / "repo"
        if repo.exists():
            shutil.rmtree(repo.parent)
        repo.parent.mkdir(parents=True)
        shutil.copytree(chain / "repo", repo, symlinks=True,
                        ignore=shutil.ignore_patterns(".venv", ".anaxigraph-v2", "__pycache__"))  # fmt: skip
        sh(["uv", "sync", "--locked", "--quiet"], cwd=repo, timeout=600, env={"VIRTUAL_ENV": ""})
        saved_as = f"docs/requests/probe-{request.name}"
        shutil.copy2(request, repo / saved_as)
        base = commit(repo, f"Probe {request.stem}")
        baseline_inputs = collector.inventory(repo)
        agent = run_agent(
            config, repo, chain / "logs" / f"probe-{request.stem}.jsonl",
            prompt_for(ARMS["bare"], request, saved_as), False,
        )  # fmt: skip
        head = commit(repo, f"Probe {request.stem} done")
        out = chain / "eval" / f"probe-{request.stem}"
        checked = repair_ci(
            config,
            repo,
            out / "safety",
            prompt_for(ARMS["bare"], request, saved_as),
            agent,
            config.last_step,
            base,
            head,
            baseline_inputs=baseline_inputs,
            acceptance_files=[
                *scenarios.acceptance_files(config.scenario, config.last_step),
                f"probe_{number}.py",
            ],
        )
        head, agent = checked["commit"], checked["agent"]
        postcheck_started = time.monotonic()
        diff = metrics.diff_stats(repo, base, head)
        changed_prod = [
            p for p in diff["changed_files"] if p.endswith(".py") and not metrics.is_test_path(p)
        ]
        selected, total = selection_size(repo, diff["changed_files"])
        row = {
            "probe": request.stem,
            "arm": arm_name,
            "agent": agent,
            "hidden_probe": hidden_subset(checked["hidden"], [f"probe_{number}.py"]),
            "hidden_regression": hidden_subset(
                checked["hidden"], scenarios.acceptance_files(config.scenario, config.last_step)
            ),
            "own_suite": checked["own_suite"],
            "diff": diff,
            "changed_prod": changed_prod,
            "ci_selected": selected,
            "ci_total": total,
            "ci": checked["ci"],
            "ci_gate": checked["ci_gate"],
            "initial_ci_gate": checked["initial_ci_gate"],
            "ci_attempts": checked["ci_attempts"],
            "verification_seconds": checked["verification_seconds"],
        }
        row["verification_seconds"] += time.monotonic() - postcheck_started
        if checked["ci_gate"]["status"] != "passed":
            write_json(pending, row)
            raise RuntimeError(f"Probe {request.stem} failed bounded CI safety repair")
        append_jsonl(results, row)
        print(f"[{chain.name}] probe {request.stem}: {row['hidden_probe']['by_file']}", flush=True)


def preflight_runtime(config) -> None:
    """Exercise the required gate before any paid coding session or state publication."""
    with tempfile.TemporaryDirectory(prefix="anaxigraph-runtime-preflight-") as folder:
        root = Path(folder)
        (root / "service.py").write_text("def operation():\n    return 1\n")
        snapshot = (runtime(config, root, "scan", timeout=30).get("snapshot") or {}).get("id")
        if not snapshot:
            raise ValueError("AnaxiGraph runtime could not scan the preflight repository")
        checked = sh(
            [
                str(config.runtime),
                "--repo",
                str(root),
                "validate",
                "--baseline-id",
                snapshot,
                "--require-ci-evidence",
            ],
            check=False,
            timeout=30,
        )
        try:
            value = json.loads(checked.stdout)
        except ValueError:
            value = {}
        if not (
            checked.returncode == 1
            and isinstance(value, dict)
            and value.get("completion_allowed") is False
            and value.get("validation_status") == "incomplete"
            and value.get("ci_selection", {}).get("required") is True
        ):
            raise ValueError(
                "Runtime lacks the enforced CI safety gate; use the updated AnaxiGraph checkout before starting paid coding"
            )


def provenance(config) -> dict:
    def out(argv):
        try:
            return sh(argv, check=False).stdout.strip()
        except OSError:
            return None

    return {
        "scenario_identity": config.scenario_identity,
        "harness_sha256": scenarios.digest(
            {
                path.name: path.read_text()
                for path in (
                    Path(__file__),
                    HERE / "metrics.py",
                    HERE / "scenarios.py",
                    HERE / "charter.md",
                    HERE / "collect_evidence.py",
                    HERE / "continuation.py",
                    HERE / "mutation_state.py",
                    HERE / "process_guard.py",
                    Path(getfile(collector.audit_selection)),
                )
            }
        ),
        "model": config.model,
        "effort": config.effort,
        "agent": config.agent,
        "codex_version": out(["codex", "--version"]),
        "harness_commit": out(["git", "-C", str(HERE), "rev-parse", "HEAD"]),
        "runtime": str(config.runtime),
        "runtime_version": out([str(config.runtime), "--version"]),
        "runtime_commit": out(
            ["git", "-C", str(Path(config.runtime).resolve().parents[2]), "rev-parse", "HEAD"]
        ),
        "review_steps": sorted(config.review_steps),
        "review_kinds": list(config.review_kinds),
        "workflow_config_sha256": scenarios.digest(config.workflow_config.read_text())
        if config.workflow_config
        else None,
        "ci_repair_attempts": config.ci_repair_attempts,
        "ci_gate_protocol": "coverage-consumers-and-actual-diff-v1",
        "evaluation_resource_policy": metrics.RESOURCE_POLICY,
        "measurement_settings": {
            "mutants": config.mutants,
            "ci_every": config.ci_every,
            "agent_timeout": config.agent_timeout,
        },
    }


def validate_resume(config, chain: Path) -> None:
    """Unknown or changed experiment inputs cannot be joined to a frozen chain."""
    if not chain.exists():
        return
    metadata = chain / "chain.json"
    if not metadata.is_file():
        raise ValueError(f"Cannot resume {chain.name}: missing frozen chain provenance")
    previous = json.loads(metadata.read_text())
    keys = (
        "scenario_identity",
        "harness_sha256",
        "model",
        "effort",
        "agent",
        "runtime",
        "runtime_version",
        "runtime_commit",
        "codex_version",
        "review_steps",
        "review_kinds",
        "workflow_config_sha256",
        "ci_repair_attempts",
        "ci_gate_protocol",
        "evaluation_resource_policy",
        "measurement_settings",
    )
    changed = [
        key for key in keys if key not in previous or previous[key] != config.provenance[key]
    ]
    if previous.get("scenario") != config.scenario:
        changed.append("scenario")
    continuation = getattr(config, "continuation", None)
    if continuation and previous.get("continuation") != {
        key: continuation[key] for key in ("protocol", "additional_ci_repairs")
    }:
        changed.append("continuation")
    if changed:
        raise ValueError(
            f"Cannot resume {chain.name}: incompatible frozen inputs: {', '.join(changed)}"
        )


def parse_steps(text: str) -> tuple[int, int]:
    first, _, last = text.partition("-")
    return int(first), int(last or first)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", type=Path)
    parser.add_argument(
        "--continue-from", type=Path, help="Fork a frozen campaign into a new exploratory state"
    )
    parser.add_argument(
        "--fork-only",
        action="store_true",
        help="Create or verify an exploratory fork without launching coding agents",
    )
    parser.add_argument(
        "--continuation-audit",
        type=Path,
        help="Attach a transcript-hash-verified public-documentation contamination audit",
    )
    parser.add_argument(
        "--additional-ci-repairs",
        type=int,
        default=3,
        help="Explicit extra repair allowance for every arm in an exploratory continuation",
    )
    parser.add_argument("--track", choices=scenarios.TRACK_IDS, default="switchboard")
    parser.add_argument(
        "--list-tracks", action="store_true", help="List executable and draft tracks without models"
    )
    parser.add_argument(
        "--describe-track",
        action="store_true",
        help="Print the selected task specification without models",
    )
    parser.add_argument(
        "--export-track",
        type=Path,
        help="Export the selected specification to a new JSON file without models",
    )
    parser.add_argument("--arms", default="bare,charter,anaxigraph")
    parser.add_argument("--replicates", type=int, default=1)
    parser.add_argument(
        "--steps", help="Inclusive range; defaults to the selected track's full history"
    )
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", default="high")
    parser.add_argument(
        "--agent",
        default="codex",
        help="codex, noop, copy:<dir> or ref:<git repo with step-NN tags> (harness tests)",
    )
    parser.add_argument(
        "--agent-timeout",
        type=float,
        help="Seconds per coding session; defaults to the track's limit (Switchboard 5400)",
    )
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--workflow-config", type=Path)
    parser.add_argument(
        "--review-steps",
        default="6,11,16",
        help="Broad review checkpoints; blank disables scheduled reviews",
    )
    parser.add_argument(
        "--review-kinds",
        default="patterns",
        help="Comma-separated patterns,architecture,ci,fresh-eyes; Patterns includes CI",
    )
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--mutants", type=int, default=10)
    parser.add_argument(
        "--ci-every",
        type=int,
        default=1,
        help="Legacy measurement cadence; required safety verification runs every request from the track's CI request",
    )
    parser.add_argument(
        "--ci-repair-attempts",
        type=int,
        choices=range(4),
        default=2,
        help="Maximum fresh repair sessions per failed external CI safety gate (0..3)",
    )
    parser.add_argument("--no-probes", dest="probes", action="store_false")
    config = parser.parse_args()
    if config.list_tracks:
        print(
            json.dumps(
                [
                    {key: track[key] for key in ("id", "name", "status", "readiness_note")}
                    for track in map(scenarios.describe, scenarios.TRACK_IDS)
                ],
                indent=2,
            )
        )
        return
    track = scenarios.describe(config.track)
    if config.describe_track or config.export_track:
        if config.export_track:
            scenarios.export(track, config.export_track)
        if config.describe_track:
            print(
                json.dumps(
                    {"track": track, "scenario_identity": scenarios.identity(track)}, indent=2
                )
            )
        return
    if track["status"] != "executable":
        parser.error(
            f"{config.track} is specification-only: public behavior contracts and per-step "
            "external acceptance checks are incomplete. No benchmark or model was launched. "
            "Use --describe-track or --export-track PATH."
        )
    if config.state is None:
        parser.error("--state is required for benchmark execution")
    config.state = config.state.resolve()
    configure_track(track)
    config.agent_timeout = float(config.agent_timeout or scenarios.layout(track)["agent_timeout"])
    config.scenario = scenarios.requirements(track)
    config.scenario_identity = scenarios.identity(track)
    config.steps = config.steps or f"{config.scenario['steps'][0]}-{config.scenario['steps'][-1]}"
    try:
        config.first_step, config.last_step = parse_steps(config.steps)
    except ValueError:
        parser.error("--steps must be an integer or an inclusive range such as 1-20")
    if not (
        config.scenario["steps"][0]
        <= config.first_step
        <= config.last_step
        <= config.scenario["steps"][-1]
    ):
        parser.error("--steps must be within the selected scenario's history")
    config.review_steps = {int(s) for s in config.review_steps.split(",") if s}
    config.review_kinds = tuple(
        dict.fromkeys(s.strip() for s in config.review_kinds.split(",") if s.strip())
    )
    if set(config.review_kinds) - {"patterns", "architecture", "ci", "fresh-eyes"}:
        parser.error("review kinds must be patterns,architecture,ci,fresh-eyes")
    arms = [a.strip() for a in config.arms.split(",") if a.strip()]
    unknown = set(arms) - set(ARMS)
    if unknown:
        parser.error(f"unknown arms: {sorted(unknown)}")
    if len(arms) != len(set(arms)):
        parser.error("--arms must not repeat an arm; each chain needs one writer")
    if not arms or config.replicates < 1 or config.concurrency < 1 or config.ci_every < 1:
        parser.error("Provide nonempty arms and positive replicates, concurrency and CI cadence")
    if config.agent != "noop" and any(ARMS[arm]["anaxigraph"] for arm in arms):
        try:
            preflight_runtime(config)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            parser.error(str(error))
    config.provenance = provenance(config)
    import continuation

    try:
        if config.additional_ci_repairs < 1:
            raise ValueError("Additional continuation repairs must be positive")
        continuation.prepare(config, sys.modules[__name__])
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if config.fork_only:
        if not config.continuation:
            parser.error(
                "--fork-only requires an explicit continuation source or existing manifest"
            )
        print(
            json.dumps(
                {
                    "status": "forked",
                    "state": str(config.state),
                    "protocol": config.continuation["protocol"],
                    "paid_calls": 0,
                }
            )
        )
        return
    chains = [(arm, r) for r in range(config.replicates) for arm in arms]
    try:
        for arm, replicate in chains:
            validate_resume(config, config.state / f"{arm}-r{replicate}")
    except ValueError as error:
        parser.error(str(error))
    write_json(
        config.state / f"run-{int(time.time())}.json", {"argv": sys.argv, **config.provenance}
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.concurrency) as pool:
        failed = False
        futures = {pool.submit(run_chain, config, arm, r): (arm, r) for arm, r in chains}
        for future in concurrent.futures.as_completed(futures):
            arm, r = futures[future]
            try:
                result = future.result()
                failed |= result.get("status") != "completed"
                print(f"chain {arm}-r{r} finished: {result}", flush=True)
            except Exception as error:  # keep other chains running
                failed = True
                print(f"chain {arm}-r{r} FAILED: {error!r}", flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
