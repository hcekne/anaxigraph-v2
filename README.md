# AnaxiGraph V2

AnaxiGraph helps people and coding agents write better code, make sound architecture decisions,
and leave subsequent changes straightforward.

Its architectural advice favors clear ownership, small public contracts and independently testable
components. The map measures public surfaces, private cross-module references, dependency cycles and
observed test links. These are evidence for decisions, not proof that a change cannot break callers.
Functions, objects and modular monoliths are all valid ways to preserve a useful boundary.
The [component architecture principles](docs/component-architecture-principles.md) explain the
house analogy: cohesive packages can be independent units of change and verification within one
process. Behavioral contracts and shared data/configuration matter as well as method signatures.
Current test links guide investigation; they do not generate a verified CI selection.
New Pattern runs also execute three targeted architectural scouts: component boundaries,
standalone packages/repositories and independent verification. Their current findings feed change
guidance; plan assessment requires an adopt/defer/reject reason for material recommendations.
The [scout workflow and limits](docs/change-request-architecture-scouts.md) distinguish those
mechanical requirements from verified implementation or automatic CI selection.
Private-access checks cover resolved function, class and method references; private globals and
instance attributes can be missed, so no finding does not establish complete boundary coverage.

This is the clean replacement core targeting **anaxigraph 1.0**. The first milestone is a local
`1.0.0a1` alpha, separate from the existing 0.9.1 package. Features are added selectively; this
folder does not import the old runtime or automatically replace an installed service.

Standalone repository: [hcekne/anaxigraph-v2](https://github.com/hcekne/anaxigraph-v2).
The local checkout is `~/repos/anaxigraph-v2`.

## Run with uv

From this repository:

```bash
cd ~/repos/anaxigraph-v2
uv sync --locked
uv run anaxigraph scan --repo /absolute/path/to/your/repo
uv run anaxigraph serve --repo /absolute/path/to/your/repo
```

Open <http://127.0.0.1:8766>. The dashboard uses the same application as CLI and MCP. It includes a
sortable module table, expandable evidence, focused dependency graph, agent-review workflows and
change guidance. Structural results are available before semantic analysis.

From another working directory, use `uv run --project /absolute/path/to/anaxigraph-v2 anaxigraph ...`.
`--project` selects the environment; it does not change the working directory or the target repo.
Always give `--repo` explicitly in agent configuration. See [uv's command reference](https://docs.astral.sh/uv/reference/cli/).

## Connect an agent through MCP

Use this stdio configuration, replacing both absolute paths:

```json
{
  "mcpServers": {
    "anaxigraph-v2": {
      "command": "uv",
      "args": [
        "run", "--project", "/absolute/path/to/anaxigraph-v2",
        "anaxigraph", "--repo", "/absolute/path/to/your/repo", "mcp"
      ]
    }
  }
}
```

The everyday loop is `scan` → `prepare_change` → focused `inspect` → edit → `scan` →
`validate_change`. `assess_plan` checks proposed file placement before a substantial change.
Preparation pins the baseline for the current task and Git HEAD. A new task or HEAD starts a fresh
comparison; repeated scans within the same task retain it. Use `continue_task=true` (CLI
`--continue-task`) to explicitly continue across those changes, or select `baseline_id` directly.
Validation reports both comparison identities.
`modules` lists the inventory; `reviews` finds saved proposals. The server never executes target
project code. Test/type/lint receipts supplied by an agent or CI are attributed reports, bound to
the exact snapshot ID. Missing checks remain unchecked; stale receipts cannot pass the candidate.

`serve` also exposes the same MCP tools over Streamable HTTP at
`http://127.0.0.1:8766/mcp` (or your selected port). A local shared service can keep an existing
HTTP client connection while switching its runtime to V2; the dashboard and MCP use one Application.
Stdio remains available for an agent tied to a specific checkout.

For unattended Codex use of your trusted local AnaxiGraph server, authorize its tools specifically:

```toml
[mcp_servers.anaxigraph]
url = "http://127.0.0.1:8766/mcp"
default_tools_approval_mode = "approve"
```

This scoped setting is separate from normal command approvals. Without it, a client configured
to reject approval prompts can attach the server yet reject every tool call. Verify a successful
`scan` and `prepare_change`, including server-side evidence, before relying on an automated trial.

When replacing an older service with custom source exclusions, pass
`--exclusions /path/to/exclusions.json` on every CLI/server invocation. The JSON array contains
repository-relative glob patterns. The same policy applies to scans and live-source checks;
V2 does not silently interpret the legacy `.anaxigraph.yml` provider configuration.

## Run Mapping, Patterns, Component Architecture or Fresh Eyes

The dashboard's **Run full mapping**, **Run full pattern analysis**, **Run full component review**
and **Run full Fresh Eyes** buttons execute the workflows. New defaults use **Luna/low** for mapping,
architectural scouts and ID-only pattern
screening, **Sol/medium** for shortlisted detailed reviews, and a pool of **32** independent workers.
Positive architectural findings receive a separate Sol/medium review by default; keep/unknown-only
scout drafts skip it. The architecture finding review model is configurable; explicit ordered review
passes replace its default. Recording an adopt decision still does not verify implementation.
Existing explicit model selections remain unchanged. Choose Codex or Claude and a model for each stage, parallel workers
(1–100), optional stronger fallbacks and ordered review passes. Installed provider CLIs must already
be authenticated. A blank model uses the local CLI's selected default. Independent batches run in
parallel; enabled OpenAI and Claude review passes remain ordered within each batch.
The default model-call deadline is 600 seconds. Set `timeout_seconds` to `null` (leave the dashboard
field blank) to wait without an elapsed deadline. Output/storage limits still apply. Codex workers
can explicitly set `reasoning_effort` to `low`, `medium`, `high` or `xhigh`; the dashboard exposes the
same choice. The evidence ledger distinguishes that requested CLI setting from an unverified
provider default. Claude workers do not accept this Codex-specific setting.
Modules containing only documentation, comments or `pass` are observed as inert: their annotations
can have empty capabilities, and Fresh Eyes retains their coverage without inventing requirements.
Capability and contract statements must be behavior-only; source references belong in separate
evidence fields. Older briefs that leak implementation identities are marked stale individually.
Portable analyses can survive unrelated documentation or Python changes when their rebuilt scoped
inputs match exactly. Seed data, configuration, frontend resources and cited documents remain guarded;
older records without that resource-context proof require a complete source match.
Workflow status retains batch attempts, retries and recent failure details after a later success.
`failed_attempts` counts recorded failures, including fallback candidates; it can exceed batch attempts.
When `attempt_history_complete` is false, older failure details are unknown. Provider `model_call`
completion and `analysis_result` acceptance/rejection are separate evidence events.

The same workflows are available to coding agents through MCP and the CLI:

```bash
uv run anaxigraph run mapping --repo /path/to/repo
uv run anaxigraph run patterns --repo /path/to/repo
uv run anaxigraph run architecture --repo /path/to/repo
uv run anaxigraph run fresh-eyes --repo /path/to/repo
uv run anaxigraph runs --repo /path/to/repo
uv run anaxigraph resume RUN_ID --repo /path/to/repo
```

CLI runs wait for completion. HTTP/MCP runs return a durable run ID and progress. Progress shows
completed/total batches, active workers, elapsed time, failures and **Final reconciliation**.
Accepted results are saved immediately; resume retries unfinished batches. A repeated unchanged
workflow reuses compatible accepted results with the same model/review pipeline.
Per-batch timestamps and cumulative wall durations include failed attempts. Model-call time and
tokens are separate from local/cache work. Pattern review shows screening and detailed review as
separate phases. Three focused architecture scouts per group run alongside screening. Fresh Eyes
runs only when requested, outside the mapping-plus-Patterns path.

Save model settings in the dashboard or through `workflow-config --file settings.json`. For example:

```json
{
  "workers": 32,
  "timeout_seconds": null,
  "pattern_mode": "screened",
  "stages": {
    "annotate": {
      "provider": "codex",
      "model": "gpt-6-luna",
      "reasoning_effort": "low",
      "fallbacks": [{"provider": "codex", "model": "gpt-6.1-sol", "reasoning_effort": "medium"}]
    },
    "pattern-screen": {"provider": "codex", "model": "gpt-6-luna", "reasoning_effort": "low"},
    "architecture": {"provider": "codex", "model": "gpt-6-luna", "reasoning_effort": "low"},
    "architecture-review": {"provider": "codex", "model": "gpt-6.1-sol", "reasoning_effort": "medium"},
    "patterns": {"provider": "codex", "model": "gpt-6.1-sol", "reasoning_effort": "medium"},
    "fresh-eyes": {"provider": "claude", "model": "YOUR_DESIGN_MODEL"}
  },
  "review_passes": []
}
```

Use model names supported by your installed CLI. Workers receive only their evidence packet in an
isolated temporary directory with tools, MCP integrations and repository instructions disabled.
Fresh Eyes receives behavior and the source-independent charter, without source identities or topology. Its subsystem designs
feed independent product-level design through functional briefs, preserving three alternatives at
each level. Final reconciliation checks declared coverage; it does not prove novelty or correctness.

Individual request/submit control remains available:

```bash
uv run anaxigraph request annotate --repo /path/to/repo --scope src/example.py > /tmp/task.json
# Give the packet and response_schema to a coding agent; save its JSON response.
uv run anaxigraph submit REQUEST_ID /tmp/result.json --repo /path/to/repo
uv run anaxigraph request pattern-screen --repo /path/to/repo --scope src/example.py
# For manual detail, pass selected catalogue IDs; omitting --pattern-ids requests an exhaustive task.
uv run anaxigraph request patterns --repo /path/to/repo --scope src/example.py --pattern-ids adapter
uv run anaxigraph request fresh-eyes --repo /path/to/repo
uv run anaxigraph reviews --repo /path/to/repo
uv run anaxigraph inspect review:REVIEW_ID --repo /path/to/repo
```

Requests survive application restart. Explicit scopes accept module IDs or relative paths. Manual
annotation requests default to the first module; pattern requests default to an eligible production
module. Default Pattern workflows cover production modules, excluding tests and inert markers.
Active package facades stay eligible, and explicit scopes may include tests. Set `pattern_mode` to
`"exhaustive"` for the full written matrix. Neither exclusion nor non-selection proves irrelevance.
Workflow annotation and Pattern jobs default to one module; blind subsystems default to sixteen.
Budgets apply per job (configurable 2 MB input and 8 MB result defaults), rather than requiring a
whole repository to fit in 256 KB. Large repositories are partitioned and reconciled. Ordinary
inventories and review lists stay compact and paged.
Saved reviews retain their original results and disclose stale source, semantic, catalogue or
prompt inputs separately from structural readiness.

- **Annotations:** one module sentence, a short paragraph, one sentence for every supplied function,
  capabilities, uncertainty and contracts with declared/inferred basis and source references.
- **Patterns:** cheaply screen the 133-card reference catalogue using the architecture charter,
  structural evidence and concise module understanding. Return only zero to ten plausible IDs;
  write detailed ratings, benefits, costs, alternatives and evidence for those selections. Empty
  shortlists make no strong-model call. Reconciliation distinguishes screened, selected, detailed
  and excluded scope. Positive ratings require a concrete incremental improvement. Explicit
  exhaustive requests remain available; missing or duplicate declared detail pairs are rejected.
- **Fresh Eyes:** functional statements with opaque requirement IDs and the source-independent
  charter. Give the returned packet
  to a fresh worker **without repository/source tools**. Do not append source or dependency context.
  Each of three alternatives must cover all supplied requirements, explain tradeoffs and include a
  high-level Mermaid diagram. Internal workers have tool access disabled. An externally supplied
  agent remains responsible for honoring its packet's isolation requirements. The service cannot
  prove architectural novelty; it retains all alternatives and separates later source-aware comparison.

The dashboard can copy these tasks, accept result JSON and display saved proposals. Pattern ratings
remain hypotheses. Source-aware verification of a frozen alternative is separate from blind design.
The [architecture-scout qualification](docs/change-request-architecture-scouts.md) records the current
workflow on an isolated twelve-module Snow Rankings copy: **107.2 seconds** for a cold map plus
Patterns including mandatory scouts, then **1.1 seconds** unchanged with no model calls. Its single
portable record stayed at **109,914 bytes**. Small recognition cases retain both a failed initial
recommendation and the subsequent reviewed results. These are measured examples, not general
latency or code-quality guarantees. Targets do not impose worker deadlines.
The [earlier focused-review qualification](docs/change-request-focused-reviews.md#completed-qualification--27-october-2026)
preserves the preceding protocol's 83.1-second result and 63.5-second leaf edit; that workload did
not include the new scouts. A controlled edit cycle has not been remeasured for the new protocol.

## What the first milestone supports

Python/PYI structure, signatures, scoped imports/calls/references, module dependency projections,
observed test links, compact saved semantics, basic lexical/symbol guidance, placement assessment,
current/previous comparison, and agent/CI check attribution. Source is parsed, never imported.

Working-tree fingerprints include eligible untracked changes and distinguish exact worktrees.
Status describes the saved snapshot; scan after editing to refresh source facts.
Dynamic targets, parse errors and unsupported languages remain explicit. JavaScript/TypeScript
are fingerprinted but **not parsed** yet. Missing references do not establish dead code. Placement
assessment is advisory; behavioral correctness and architectural usefulness require real review.

Unchanged files reuse verified fingerprints and parser observations within the service. Annotation
writes update one search document. Dependency freshness uses public signatures, docstrings and
exports. Body-only changes preserve caller annotations and separately flag changed implementations;
preserved annotations do not prove unchanged behavior. Own-source edits still invalidate that module.
Fresh CLI processes start with cold caches.

## Carry understanding through Git

After an explicit export or full workflow, the target repository contains **`anaxigraph/record.json`**. It carries
concise module/function understanding, saved analyses, input identities and provenance. It contains
no source bodies, SQL database, deterministic dependency graph or search cache. Commit and push
`anaxigraph/` with your normal Git workflow. Another clone builds its local structural index and
automatically reuses compatible annotations and analyses from this record without new model calls.
Changed inputs remain stale; local current understanding takes precedence. Product reviews retain
their subsystem lineage. Unchanged exports are byte-stable and create no additional partitions.

```bash
uv run anaxigraph export --repo /path/to/repo
uv run anaxigraph import --repo /path/to/repo
uv run anaxigraph scan --export --repo /path/to/repo
```

Manual submissions save locally; export explicitly after a manual session. Full workflows export
at completion or failure, preserving accepted batches. Mapping, exporting and committing/pushing
are separate states. Status measures the record against the local Git commit and configured
remote-tracking reference, explicitly reporting unknowns and that no remote was contacted. Ordinary
scans update the local index and may reuse an existing record; they do not create one automatically.
Source packages named `anaxigraph` remain indexable alongside recognized portable artifacts.
Ignore `.anaxigraph-v2/` in Git for local caches and trial state; track `anaxigraph/` when you
explicitly publish portable understanding.

Local state lives in `.anaxigraph-v2/knowledge.sqlite`: current and previous graphs plus one pinned
change baseline, canonical annotations and compressed review packets. Current batch results are
retained by scope rather than discarded after twenty reviews. The SQLite ceiling is 100 MB;
portable publication also budgets for active local state, targeting 100 MB combined. Workflow
progress has bounded file count and size, with a 200 MB retained-state guard. Capacity failure
preserves the last valid record. A schema migration keeps one pre-migration SQLite backup.

Legacy paid-analysis import, shared worktree bases, production deployment and additional languages
remain later choices. Existing legacy saved analyses are left intact. Full old-feature parity is not
the objective. See the [change request and acceptance checks](docs/change-request-workflows.md).

## Components and tests

Eight components have explicit boundaries: contracts, indexing, storage, analysis, workflow execution,
local evidence, application and interfaces. See [architecture and packaging direction](docs/architecture.md).

Task preparation, explicitly inspected entities, validation reports and actual model calls produce
bounded local evidence in `.anaxigraph-v2/trial.sqlite` (5 MB). Supplied context and requested
inspections are distinct; neither proves the agent understood a result. Codex/Claude CLI usage is
captured when reported; missing tokens or dollar costs remain unknown. Read or export it with
`anaxigraph evidence` / `anaxigraph evidence --export`, or the equivalent MCP/HTTP tools. No external
telemetry is sent; `ANAXIGRAPH_EVIDENCE=off` disables recording. The separate
[Snow Rankings experiment](docs/snow-rankings-experiment.md) compares the same product tasks with
and without AnaxiGraph using the same GPT-6.1-Sol/xhigh coding model.

From this folder:

```bash
uv sync --locked --extra dev
uv run pytest
uv run ruff check src tests
uv run mypy
uv run python scripts/quality.py
uv build
```

Tests cover actual Git/worktrees, unresolved references, malformed/incomplete results, dependency
freshness, interrupted/repeated persistence, storage-full rollback, source-blind packets, receipts,
transport contracts and component import boundaries. Browser and actual MCP stdio smoke evidence
is recorded separately from unit tests and live model behavior. No claim of improved agent success
or architectural novelty is made from these checks alone.

## PyPI destination

The existing project name remains `anaxigraph`; this folder has its own uv environment and alpha
metadata. Once the selected product is qualified, it can replace the release-root implementation
and publish as `anaxigraph==1.0.0` using the existing publisher. Older versions need not be deleted.
No new release is published by this milestone.
