# AnaxiGraph

AnaxiGraph helps people and coding agents understand a codebase, choose useful architectural
improvements, and keep later changes manageable.

It combines a compact code graph with concise module explanations, focused architectural and
pattern reviews, implementation-blind Fresh Eyes designs, and CI/CD guidance. Use the module table
and dependency views yourself, or connect an agent through MCP. Both use the same saved understanding.

V2 is the replacement core targeting AnaxiGraph 1.0. This repository currently builds `1.0.0a1`;
it has not replaced the published legacy package. Python/PYI is parsed; JavaScript/TypeScript
files are tracked but their code relationships are not yet parsed.

## Start with uv

```bash
git clone https://github.com/hcekne/anaxigraph-v2.git
cd anaxigraph-v2
uv sync --locked
uv run anaxigraph scan --repo /absolute/path/to/your/repo
uv run anaxigraph serve --repo /absolute/path/to/your/repo
```

Open <http://127.0.0.1:8766>. Start **Mapping** to add semantic understanding; the structural map
is available immediately after a scan. Authenticate your installed Codex or Claude CLI before
running model workflows. The dashboard lets you choose models, reasoning effort and parallel workers.

To run from another directory, use `uv run --project /absolute/path/to/anaxigraph-v2 anaxigraph ...`.
Give `--repo` explicitly so the right checkout is used.

## Connect a coding agent

Replace the two paths in your MCP client's configuration:

```json
{
  "mcpServers": {
    "anaxigraph": {
      "command": "uv",
      "args": [
        "run", "--project", "/absolute/path/to/anaxigraph-v2",
        "anaxigraph", "mcp", "--repo", "/absolute/path/to/your/repo"
      ]
    }
  }
}
```

A running dashboard also serves MCP at `http://127.0.0.1:8766/mcp`. Verify that the agent can call
`scan` and `prepare_change`; some clients require explicit authorization for MCP tools.
See [connection and remote-access instructions](docs/usage.md#connections).

## Recommended routine

| When | Use AnaxiGraph to |
| --- | --- |
| First use or a fresh checkout | Scan and run Mapping. Reuse compatible records carried through Git. |
| Each substantial coding task | Prepare the change, inspect relevant owners/contracts, then assess the plan. |
| After editing | Scan again, run appropriate project checks, and validate against the original task baseline. |
| A completed feature or meaningful refactor | Run Patterns to look for better boundaries, packages, tests and applicable patterns. |
| A milestone, architectural change, or weekly on an active project | Run Fresh Eyes for three alternative designs based on functionality. |
| Component, shared-build or pipeline changes | Review CI/CD and update the declared check policy where needed. |

These are starting habits, not a requirement to run every model workflow after every edit.
Mapping refreshes changed understanding; repeated unchanged workflows reuse compatible results.
Pattern runs include focused architecture scouts and one CI/CD review. Fresh Eyes is separate.
Change preparation works after a structural scan, combining source, symbols and current summaries.
It preserves three owner candidates and labels historical co-change suggestions separately.

[The usage guide](docs/usage.md) includes an instruction block for your project's `AGENTS.md`,
reviewing recommendations, choosing a sensible cadence, and collecting feedback from agents.

## Carry the understanding with your code

Completed workflows export portable understanding to **`anaxigraph/record.json`**. Commit that
folder to share compatible analyses between development machines and scheduled cloud jobs.
Keep the local `.anaxigraph-v2/` cache out of Git. Ordinary structural scans do not publish a record;
use `anaxigraph export` after manual analysis submissions.

AnaxiGraph favors cohesive components with clear public contracts and independent tests, usually
composed in one process. Separate packages and repositories are options when they make maintenance
and agent work easier. Recommendations are proposals to investigate; project checks establish
whether the resulting implementation works.

## Read further

- [Usage and agent instructions](docs/usage.md)
- [Component architecture principles](docs/component-architecture-principles.md)
- [CI policy format and examples](docs/change-request-ci-delivery.md#declare-accepted-tasks)
- [Architecture and component contracts](docs/architecture.md)
- [Measured results and known limits](docs/evidence/ci-delivery-qualification.json)
- [Architectural advice and later-change experiment](experiments/architecture_quality/README.md)
- [Owner-finding replay and inexpensive baselines](experiments/owner_replay/README.md)

For development: `uv sync --locked --extra dev`, then `uv run pytest`, `uv run ruff check src tests`,
`uv run mypy` and `uv run python scripts/quality.py`. Slow checks stay in CI rather than pre-push hooks.
