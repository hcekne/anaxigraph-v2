# AnaxiGraph

AnaxiGraph helps people and coding agents understand a codebase, choose useful architectural
improvements, and keep later changes manageable.

It combines a compact code graph with concise module explanations, focused architectural and
pattern reviews, implementation-blind Fresh Eyes designs, and CI/CD guidance. Use the module table
and dependency views yourself, or connect an agent through MCP. Both use the same saved understanding.

The 1.0 line replaces the legacy 0.9.x implementation. It parses Python/PYI; JavaScript/TypeScript
files are inventoried without parsed code relationships. See the
[upgrade guide](https://github.com/hcekne/anaxigraph-v2/blob/v1.0.0/docs/usage.md#upgrading-from-09x)
for CLI, MCP and state changes, and check the
[release record](https://github.com/hcekne/anaxigraph-v2/releases/tag/v1.0.0).

## Start with uv

With [uv](https://docs.astral.sh/uv/) and Python 3.11 or newer, run the PyPI package:

```bash
uvx --from anaxigraph==1.0.0 anaxigraph scan --repo /absolute/path/to/your/repo
uvx --from anaxigraph==1.0.0 anaxigraph serve --repo /absolute/path/to/your/repo
```

Open <http://127.0.0.1:8766>. The structural map is available after scanning. Start **Mapping**
to add semantic understanding, after authenticating your installed Codex or Claude CLI.
The dashboard lets you choose models, reasoning effort and parallel workers.
Always give `--repo` explicitly so the intended checkout is analyzed.

For a persistent command, install once and upgrade when needed:

```bash
uv tool install 'anaxigraph>=1.0.0'
anaxigraph scan --repo /absolute/path/to/your/repo
uv tool upgrade anaxigraph
```

Restart running services after upgrading. Users of 0.9.x should follow the upgrade guide first.

## Connect a coding agent

Replace the repository path in your MCP client's configuration:

```json
{
  "mcpServers": {
    "anaxigraph": {
      "command": "uvx",
      "args": [
        "--from", "anaxigraph==1.0.0", "anaxigraph",
        "mcp", "--repo", "/absolute/path/to/your/repo"
      ]
    }
  }
}
```

A running dashboard also serves MCP at `http://127.0.0.1:8766/mcp`. Verify that the agent can call
`scan` and `prepare_change`; some clients require explicit authorization for MCP tools.
See [connection and remote-access instructions](https://github.com/hcekne/anaxigraph-v2/blob/v1.0.0/docs/usage.md#connections).

## Recommended routine

| When | Use AnaxiGraph to |
| --- | --- |
| First use or a fresh checkout | Scan and run Mapping. Reuse compatible records carried through Git. |
| Each substantial coding task | Prepare the change, inspect relevant owners/contracts, then assess the plan. |
| After editing | Scan again, run appropriate project checks, and validate against the original task baseline. |
| A completed feature or meaningful refactor | Run Patterns to look for better boundaries, packages, tests and applicable patterns. |
| A milestone, architectural change, or weekly on an active project | Run Fresh Eyes for three alternative designs based on functionality. |
| Component, shared-build or pipeline changes | Review CI/CD and update the declared check policy where needed. |

Mapping refreshes changed understanding; compatible unchanged analyses are reused. Patterns includes
architecture scouts and CI/CD review; Fresh Eyes is separate. Recommendations are proposals to
investigate, while project checks establish whether the implementation works.
[The usage guide](https://github.com/hcekne/anaxigraph-v2/blob/v1.0.0/docs/usage.md) includes an `AGENTS.md` instruction block and details on preparing,
assessing and validating changes.

## Carry the understanding with your code

Completed workflows export portable understanding to **`anaxigraph/record.json`**. Commit that
folder to share compatible analyses between development machines and scheduled cloud jobs.
Keep the local `.anaxigraph-v2/` cache out of Git. Ordinary structural scans do not publish a record;
use `anaxigraph export --repo /absolute/path/to/your/repo` after manual analysis submissions.

Legacy 0.9.x records are not imported automatically. Back up existing state before a new export;
see the [upgrade guide](https://github.com/hcekne/anaxigraph-v2/blob/v1.0.0/docs/usage.md#upgrading-from-09x).

## Read further

- [Usage and upgrade guide](https://github.com/hcekne/anaxigraph-v2/blob/v1.0.0/docs/usage.md)
- [1.0.0 release record](https://github.com/hcekne/anaxigraph-v2/releases/tag/v1.0.0)
- [Component architecture principles](https://github.com/hcekne/anaxigraph-v2/blob/v1.0.0/docs/component-architecture-principles.md)
- [CI policy format and examples](https://github.com/hcekne/anaxigraph-v2/blob/v1.0.0/docs/change-request-ci-delivery.md#declare-accepted-tasks)
- [Architecture and component contracts](https://github.com/hcekne/anaxigraph-v2/blob/v1.0.0/docs/architecture.md)
- [Measured results and known limits](https://github.com/hcekne/anaxigraph-v2/blob/v1.0.0/docs/evidence/ci-delivery-qualification.json)
- [Architectural outcome experiments](https://github.com/hcekne/anaxigraph-v2/blob/v1.0.0/experiments/architecture_quality/README.md)

For source development, clone [this repository](https://github.com/hcekne/anaxigraph-v2), run
`uv sync --locked --extra dev`, then `uv run pytest`, `uv run ruff check src tests`, `uv run mypy`
and `uv run python scripts/quality.py`. Use `uv run anaxigraph` in that checkout; the package
commands above need no source clone. Slow checks stay in CI rather than pre-push hooks.
