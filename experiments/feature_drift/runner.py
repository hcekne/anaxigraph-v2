"""Run the feature-drift benchmark: one product, twenty requests, several arms in parallel.

Each chain (arm × replicate) copies the seed, then for every request:

1. saves the request under `docs/requests/` and commits it;
2. AnaxiGraph arms only: scans, refreshes semantic mapping and, at review steps, runs the
   Patterns and CI/CD reviews, so the map is current like a maintained record;
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
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics  # noqa: E402

HERE = Path(__file__).resolve().parent
SCENARIO = HERE / "scenario"
SEED, HIDDEN = SCENARIO / "seed", SCENARIO / "hidden"
REQUESTS = sorted((SCENARIO / "requests").glob("*.md"))
PROBES = sorted((SCENARIO / "probes").glob("*.md"))
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
           "anaxigraph/", ".ruff_cache/", ".uv-cache/"]  # fmt: skip

BASE_PROMPT = """You are the engineer maintaining this repository (Switchboard). A new product
request has arrived. It is saved at `{path}`; earlier requests are in `docs/requests/`.

{request}

Implement it completely in this repository, with tests. Everything earlier requests asked for
must keep working.

- There is no network access. The locked virtualenv in `.venv` is all there is; do not add
  dependencies. Run tests with `.venv/bin/python -m pytest`.
- Keep `run.json` working: the service is started from it.
- Do not commit; your work is committed when you finish. There will be no follow-up session on
  this request, so finish it now.

When done, reply with a short summary of what you changed."""

ANAXIGRAPH_PROMPT = """## AnaxiGraph

AnaxiGraph is connected as MCP tools. Its map of this repository was refreshed just before this
request (semantic mapping every request; Patterns and CI/CD reviews every few requests). Use it
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, default=str))


def append_jsonl(path: Path, value) -> None:
    with path.open("a") as stream:
        stream.write(json.dumps(value, default=str) + "\n")


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
    sh(["git", "-C", str(repo), "add", "-A"])
    sh(["git", "-C", str(repo), "commit", "--allow-empty", "-q", "-m", message])
    return sh(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip()


def prompt_for(arm: dict, request: Path, saved_as: str) -> str:
    text = BASE_PROMPT.format(path=saved_as, request=request.read_text().strip())
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
    log.parent.mkdir(parents=True, exist_ok=True)
    (log.with_suffix(".prompt.md")).write_text(prompt)
    started = time.monotonic()
    if config.agent == "noop":
        return {"status": "noop", "seconds": 0.0}
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
    try:
        result = subprocess.run(
            codex_argv(config, repo, with_anaxigraph),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=config.agent_timeout,
            env={**os.environ, **env},
        )
        raw, status = result.stdout, "completed" if result.returncode == 0 else "failed"
        stderr = result.stderr
    except subprocess.TimeoutExpired as error:
        raw = error.stdout.decode() if isinstance(error.stdout, bytes) else (error.stdout or "")
        status, stderr = "timeout", "agent timed out"
    log.write_text(raw)
    log.with_suffix(".stderr.txt").write_text(stderr or "")
    return {
        "status": status,
        "seconds": round(time.monotonic() - started, 1),
        # The benchmark lives on the same machine; flag transcripts that touched it.
        "contaminated": "feature_drift" in raw or "scenario/hidden" in raw,
        **summarize_events(raw),
    }


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
    }


def refresh_anaxigraph(config, repo: Path, step: int) -> dict:
    started = time.monotonic()
    extra = ["--config", str(config.workflow_config)] if config.workflow_config else []
    report = {"scan": bool(runtime(config, repo, "scan").get("snapshot"))}
    kinds = ["mapping"] + (["patterns", "ci"] if step in config.review_steps else [])
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
        "collection_error": result.returncode not in (0, 1) and not outcomes,
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
    files = [f"step_{n:02d}.py" for n in range(1, step + 1)]
    out = chain / "eval" / f"step-{step:02d}"
    row = {
        "hidden": hidden_tests(repo, files, step, out / "hidden.xml"),
        "own_suite": own_suite(repo, out / "own.xml"),
        "static": metrics.static_metrics(repo),
        "diff": metrics.diff_stats(repo, base, head),
    }
    if step >= 6 and (step - 6) % config.ci_every == 0 or step == config.last_step:
        row["ci"] = metrics.ci_quality(repo, seed=step, mutants=config.mutants)
        write_json(out / "ci.json", row["ci"])
        row["ci"] = {k: v for k, v in row["ci"].items() if k != "per_file"}
    return row


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
    if not repo.exists():
        prepare_repo(repo, SEED)
        commit(repo, "Seed")
        write_json(chain / "chain.json", {"arm": arm_name, **arm, "replicate": replicate,
                                          "started": now(), **config.provenance})  # fmt: skip
    done = {row["step"] for row in read_jsonl(results)}
    for request in REQUESTS:
        step = int(request.name[:2])
        if step < config.first_step or step > config.last_step or step in done:
            continue
        saved_as = f"docs/requests/{request.name}"
        (repo / "docs" / "requests").mkdir(parents=True, exist_ok=True)
        shutil.copy2(request, repo / saved_as)
        base = commit(repo, f"Request {step}")
        row = {"step": step, "arm": arm_name, "replicate": replicate, "started": now()}
        if arm["anaxigraph"] and config.agent not in ("noop",):
            row["anaxigraph"] = refresh_anaxigraph(config, repo, step)
        row["agent"] = run_agent(
            config, repo, chain / "logs" / f"step-{step:02d}.jsonl",
            prompt_for(arm, request, saved_as), arm["anaxigraph"],
        )  # fmt: skip
        head = commit(repo, f"Step {step}: {request.stem}")
        row["commit"] = head
        row.update(evaluate(config, chain, repo, step, base, head))
        row["finished"] = now()
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
    if config.probes and config.last_step == len(REQUESTS):
        run_probes(config, chain, arm_name)
    return {"chain": chain.name, "steps": len(read_jsonl(results))}


def run_probes(config, chain: Path, arm_name: str) -> None:
    results = chain / "probes.jsonl"
    done = {row["probe"] for row in read_jsonl(results)}
    for number, request in enumerate(PROBES, 1):
        if request.stem in done:
            continue
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
        agent = run_agent(
            config, repo, chain / "logs" / f"probe-{request.stem}.jsonl",
            prompt_for(ARMS["bare"], request, saved_as), False,
        )  # fmt: skip
        head = commit(repo, f"Probe {request.stem} done")
        out = chain / "eval" / f"probe-{request.stem}"
        diff = metrics.diff_stats(repo, base, head)
        changed_prod = [
            p for p in diff["changed_files"] if p.endswith(".py") and not metrics.is_test_path(p)
        ]
        targets, _ = metrics.select(repo, diff["changed_files"])
        total = len(metrics.collect(repo, ["ALL"]) or [])
        selected = (
            None
            if targets is None
            else total
            if targets == ["ALL"]
            else len(metrics.collect(repo, targets) or [])
        )
        row = {
            "probe": request.stem,
            "arm": arm_name,
            "agent": agent,
            "hidden_probe": hidden_tests(repo, [f"probe_{number}.py"], 20, out / "probe.xml"),
            "hidden_regression": hidden_tests(
                repo, [f"step_{n:02d}.py" for n in range(1, 21)], 20, out / "steps.xml"
            ),
            "own_suite": own_suite(repo, out / "own.xml"),
            "diff": diff,
            "changed_prod": changed_prod,
            "ci_selected": selected,
            "ci_total": total,
        }
        append_jsonl(results, row)
        print(f"[{chain.name}] probe {request.stem}: {row['hidden_probe']['by_file']}", flush=True)


def provenance(config) -> dict:
    def out(argv):
        try:
            return sh(argv, check=False).stdout.strip()
        except OSError:
            return None

    return {
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
    }


def parse_steps(text: str) -> tuple[int, int]:
    first, _, last = text.partition("-")
    return int(first), int(last or first)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--arms", default="bare,charter,anaxigraph")
    parser.add_argument("--replicates", type=int, default=1)
    parser.add_argument("--steps", default=f"1-{len(REQUESTS)}")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", default="high")
    parser.add_argument(
        "--agent", default="codex", help="codex, noop or copy:<dir> (harness tests)"
    )
    parser.add_argument("--agent-timeout", type=float, default=5400)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--workflow-config", type=Path)
    parser.add_argument("--review-steps", default="6,11,16")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--mutants", type=int, default=10)
    parser.add_argument("--ci-every", type=int, default=1)
    parser.add_argument("--no-probes", dest="probes", action="store_false")
    config = parser.parse_args()
    config.state = config.state.resolve()
    config.first_step, config.last_step = parse_steps(config.steps)
    config.review_steps = {int(s) for s in config.review_steps.split(",") if s}
    arms = [a.strip() for a in config.arms.split(",") if a.strip()]
    unknown = set(arms) - set(ARMS)
    if unknown:
        parser.error(f"unknown arms: {sorted(unknown)}")
    config.provenance = provenance(config)
    write_json(
        config.state / f"run-{int(time.time())}.json", {"argv": sys.argv, **config.provenance}
    )
    chains = [(arm, r) for r in range(config.replicates) for arm in arms]
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.concurrency) as pool:
        futures = {pool.submit(run_chain, config, arm, r): (arm, r) for arm, r in chains}
        for future in concurrent.futures.as_completed(futures):
            arm, r = futures[future]
            try:
                print(f"chain {arm}-r{r} finished: {future.result()}", flush=True)
            except Exception as error:  # keep other chains running
                print(f"chain {arm}-r{r} FAILED: {error!r}", flush=True)


if __name__ == "__main__":
    main()
