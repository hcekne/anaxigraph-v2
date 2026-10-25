# AnaxiGraph V2

AnaxiGraph helps people and coding agents write better code, make sound architecture decisions,
and leave subsequent changes straightforward.

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
Preparation pins the change's baseline across subsequent scans. Pass `baseline_id` explicitly to
start a new comparison; validation reports the baseline and candidate identities.
`modules` lists the inventory; `reviews` finds saved proposals. The server never executes target
project code. Test/type/lint receipts supplied by an agent or CI are attributed reports, bound to
the exact snapshot ID. Missing checks remain unchecked; stale receipts cannot pass the candidate.

## Run Mapping, Patterns or Fresh Eyes

The dashboard's **Run full mapping**, **Run full pattern analysis** and **Run full Fresh Eyes**
buttons execute the workflows. Choose Codex or Claude and a model for each stage, parallel workers
(1–100), optional stronger fallbacks and ordered review passes. Installed provider CLIs must already
be authenticated. A blank model uses the local CLI's selected default. Independent batches run in
parallel; enabled OpenAI and Claude review passes remain ordered within each batch.

The same workflows are available to coding agents through MCP and the CLI:

```bash
uv run anaxigraph run mapping --repo /path/to/repo
uv run anaxigraph run patterns --repo /path/to/repo
uv run anaxigraph run fresh-eyes --repo /path/to/repo
uv run anaxigraph runs --repo /path/to/repo
uv run anaxigraph resume RUN_ID --repo /path/to/repo
```

CLI runs wait for completion. HTTP/MCP runs return a durable run ID and progress. Progress shows
completed/total batches, active workers, elapsed time, failures and **Final reconciliation**.
Accepted results are saved immediately; resume retries unfinished batches. A repeated unchanged
workflow reuses compatible accepted results with the same model/review pipeline.

Save model settings in the dashboard or through `workflow-config --file settings.json`. For example:

```json
{
  "workers": 16,
  "timeout_seconds": 600,
  "stages": {
    "annotate": {
      "provider": "codex",
      "model": "YOUR_SMALL_MODEL",
      "fallbacks": [{"provider": "codex", "model": "YOUR_STRONGER_MODEL"}]
    },
    "patterns": {"provider": "codex", "model": "YOUR_REVIEW_MODEL"},
    "fresh-eyes": {"provider": "claude", "model": "YOUR_DESIGN_MODEL"}
  },
  "review_passes": []
}
```

Use model names supported by your installed CLI. Workers receive only their evidence packet in an
isolated temporary directory with tools, MCP integrations and repository instructions disabled.
Fresh Eyes receives behavior alone, without source identities or topology. Its subsystem designs
feed independent product-level design through functional briefs, preserving three alternatives at
each level. Final reconciliation checks declared coverage; it does not prove novelty or correctness.

Individual request/submit control remains available:

```bash
uv run anaxigraph request annotate --repo /path/to/repo --scope src/example.py > /tmp/task.json
# Give the packet and response_schema to a coding agent; save its JSON response.
uv run anaxigraph submit REQUEST_ID /tmp/result.json --repo /path/to/repo
uv run anaxigraph request patterns --repo /path/to/repo --scope src/example.py
uv run anaxigraph request fresh-eyes --repo /path/to/repo
uv run anaxigraph reviews --repo /path/to/repo
uv run anaxigraph inspect review:REVIEW_ID --repo /path/to/repo
```

Requests survive application restart. Explicit scopes accept module IDs or relative paths. Manual
annotation/Pattern requests default to the first module; workflows cover all selected modules.
Workflow annotation and Pattern jobs default to one module; blind subsystems default to sixteen.
Budgets apply per job (configurable 2 MB input and 8 MB result defaults), rather than requiring a
whole repository to fit in 256 KB. Large repositories are partitioned and reconciled. Ordinary
inventories and review lists stay compact and paged.
Saved reviews retain their original results and disclose stale source, semantic, catalogue or
prompt inputs separately from structural readiness.

- **Annotations:** one module sentence, a short paragraph, one sentence for every supplied function,
  capabilities, uncertainty and contracts with declared/inferred basis and source references.
- **Patterns:** every one of the 133 catalogue cards against every selected module, including a
  rating or unknown, current fit, specific pros, costs, a simpler alternative and evidence. Positive
  ratings require a proposed incremental change; an already useful pattern is not itself a reason
  to rewrite. Validation rejects
  missing or duplicate pairs. This establishes declared coverage, not recommendation quality.
- **Fresh Eyes:** only functional statements with opaque requirement IDs. Give the returned packet
  to a fresh worker **without repository/source tools**. Do not append source or dependency context.
  Each of three alternatives must cover all supplied requirements, explain tradeoffs and include a
  high-level Mermaid diagram. Internal workers have tool access disabled. An externally supplied
  agent remains responsible for honoring its packet's isolation requirements. The service cannot
  prove architectural novelty; it retains all alternatives and separates later source-aware comparison.

The dashboard can copy these tasks, accept result JSON and display saved proposals. Pattern ratings
remain hypotheses. Source-aware verification of a frozen alternative is separate from blind design.

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
writes update one search document. Dependency freshness uses normalized Python syntax, so a
dependency's comment-only edit does not invalidate its callers. Meaningful implementation changes
still conservatively invalidate dependent annotations. Fresh CLI processes start with cold caches.

## Carry understanding through Git

After a scan or workflow, the target repository contains **`anaxigraph/record.json`**. It carries
concise module/function understanding, saved analyses, input identities and provenance. It contains
no source bodies, SQL database, deterministic dependency graph or search cache. Commit and push
`anaxigraph/` with your normal Git workflow. Another clone builds its local structural index and
automatically reuses compatible annotations and analyses from this record without new model calls.
Changed inputs remain stale; local current understanding takes precedence. Product reviews retain
their subsystem lineage. Unchanged exports are byte-stable and create no additional partitions.

```bash
uv run anaxigraph export --repo /path/to/repo
uv run anaxigraph import --repo /path/to/repo
```

Manual submissions save locally; export explicitly after a manual session. Full workflows export
at completion or failure, preserving accepted batches. Mapping, exporting and committing/pushing
are separate states: AnaxiGraph never reports a Git push merely because it wrote a record.

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

Seven components have explicit boundaries: contracts, indexing, storage, analysis, workflow execution,
application and interfaces. See [architecture and packaging direction](docs/architecture.md).

From this folder:

```bash
uv sync --locked --extra dev
uv run pytest
uv run ruff check src tests
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
