# Using AnaxiGraph during development

AnaxiGraph is most useful when its understanding influences a real change. Use it to find the
right owner, reuse existing behavior, evaluate a smaller public interface, and choose the checks
that belong to that change. Run deeper reviews regularly enough to challenge accumulated design
choices, then give their findings to the coding agent as concrete work.

## Install and run

The 1.0 package uses the existing `anaxigraph` PyPI project, with Python 3.11 or newer.
Check the [release record](releases/1.0.0.md) for publication status. With uv installed:

```bash
uvx --from anaxigraph==1.0.0 anaxigraph scan --repo /absolute/path/to/your/repo
uvx --from anaxigraph==1.0.0 anaxigraph status --repo /absolute/path/to/your/repo
uvx --from anaxigraph==1.0.0 anaxigraph serve --repo /absolute/path/to/your/repo
```

Open `http://127.0.0.1:8766`. Scanning uses no models; Mapping and other model workflows require an
authenticated, installed Codex or Claude CLI. For a persistent executable:

```bash
uv tool install 'anaxigraph>=1.0.0'
anaxigraph scan --repo /absolute/path/to/your/repo
uv tool upgrade anaxigraph
```

The installed requirement permits later stable releases. To keep an exact version, use the pinned
`uvx --from` form instead. Restart dashboard and MCP processes after upgrades. For source development,
run `uv sync --locked` in this repository and prefix commands with `uv run`; from another directory,
use `uv run --project /absolute/path/to/anaxigraph-v2 anaxigraph ...`. Always set `--repo`.

## Upgrading from 0.9.x

1.0 is a replacement implementation with breaking CLI, MCP and state boundaries. Existing scripts,
plugin commands and client configurations need updating. Commands such as `up`, `understand` and
`plan` are not 1.0 commands; start with `scan`, `serve`, and the current `--help`. Configure stdio MCP
with `uvx --from anaxigraph==1.0.0 anaxigraph mcp --repo /absolute/path/to/your/repo` and verify an
actual `scan` or `prepare_change` call. The [README](../README.md#connect-a-coding-agent) shows the JSON.

Before trying 1.0 on an existing checkout, stop the old service and back up its local database,
configuration and tracked `anaxigraph/` records outside that checkout. Preserve the old records
before exporting new ones into the same folder. 1.0 uses `.anaxigraph-v2/knowledge.sqlite` locally
and `anaxigraph/record.json` for portable analyses. It does not import legacy 0.9.x database or record
schemas automatically. Unsupported portable formats are rejected and preserved; compatible V2
portable records can be reused when their source and analysis identities match. Scan to build a
new structural map, then run Mapping or selected reviews to build new understanding.

After backing up state, upgrade an existing uv tool installation with
`uv tool upgrade 'anaxigraph>=1.0.0'`. The version constraint also replaces an old 0.9.x pin;
reconfigure and restart services separately.

Python/PYI parsing is supported. JavaScript/TypeScript files are inventoried and fingerprinted,
without parsed relationships. The replacement selectively implements useful capabilities; it does
not restore the legacy feature set or promise compatibility with old workflow outputs.

If an existing workflow still needs 0.9.x, temporarily run the old pinned package without replacing
your installed 1.0 command:

```bash
uvx --from anaxigraph==0.9.1 anaxigraph --help
```

Use the [legacy documentation](https://github.com/hcekne/anaxigraph/tree/v0.9.1) for its commands
and keep its state separate. Previous PyPI releases remain available; a package upgrade does not
migrate an already running legacy service.

## Everyday agent loop

1. **Scan** the exact checkout. A structural scan uses no model calls. If current semantic
   understanding is missing, run Mapping; compatible unchanged understanding is reused.
2. **Prepare** a substantial task with `prepare_change`. Keep its baseline and task identity.
   The reply opens with a `design_brief`: questions to answer before planning and any closed
   families near the task (see [component principles](component-architecture-principles.md)).
   It then names likely owners, contracts, test links and relevant saved recommendations.
   Use `inspect` to expand a module or saved review; `inspect("charter")` returns the general principles.
   Entity inspection starts compact; follow `next_offset` to page relationships, or pass `full=true`
   for complete entity annotations and contracts. Plan assessment returns changes and decisions;
   `include_brief=true` explicitly repeats the preparation brief.
3. **Assess the plan** before a substantial edit. `assess_plan` continues the prepared task even
   when its description is reworded. Adopt, defer or reject relevant material architecture findings
   with a reason, including extension-point findings for plans that add a family member by
   editing its registrar. Deferring or rejecting one also needs `revisit_when`. An accepted recommendation is a decision to do work, not evidence that it is done.
4. **Implement and check** the change. Prefer local behavior and public-contract tests; include
   consumers when contracts, behavior, shared resources or configuration can affect them. Keep the
   project's required checks.
5. **Scan and validate** the candidate against the original baseline. Supply check commands,
   exit codes and the candidate snapshot ID. Explain unresolved findings or missing checks.
   Validation compares observed private accesses, cycles and consumers with the pinned baseline.
   Persisted proposal obligations receive narrow predicate checks where possible; adopting a
   proposal never establishes that its full behavior was implemented.

Start a separate task with a new `prepare_change`. Use `continue_task=true` or an explicit
`baseline_id` when continuing across changed wording or commits. Scanning alone does not advance
the pinned baseline. Source changes during a model run require a new run for the new snapshot; compatible accepted
batches are reused. Stronger models cannot fix stale inputs.

## A practical review cadence

| Work | Suggested starting cadence | What to do with the result |
| --- | --- | --- |
| Structural scan | Task start and after edits | Refresh ownership, references and exact checkout identity. |
| Semantic Mapping | Initial setup, then changed/stale modules during normal work | Read sentences first; expand only relevant details. |
| Patterns and architecture | After a feature/refactor, or several times a week during active development | Inspect useful proposals and turn selected ones into implementation tasks. |
| Fresh Eyes | Milestones and major direction changes; roughly weekly on an active project | Compare all three alternatives with requirements and current source before choosing changes. |
| CI/CD | When component boundaries, shared build inputs or workflows change; periodically if CI is slowing down | Validate scope, improve the pipeline and update check declarations. |

Frequency depends on how quickly the code is changing. Small edits usually need a refreshed scan
and targeted checks, not every model workflow. You can scope Mapping and architectural reviews to
relevant modules. Completed batches are saved immediately; resume unfinished work instead of
starting everything again.

Default semantic Mapping covers executable production modules. Tests and inert namespace markers
remain in the structural map; use explicit module scopes or configuration `include_tests: true`
to annotate them too. Progress reports needed/reused/excluded module counts. Unchanged compatible
annotations need no model calls; explicit saved model and review choices remain part of reuse.

Pattern runs include catalogue screening, detailed review of selected opportunities, and focused
workers for boundaries, package/repository options, independent verification and CI/CD. They do
not run Fresh Eyes. Fresh Eyes receives behavior summaries rather than current source or topology,
and keeps three design alternatives at subsystem and product levels.

Defaults use Luna/low for mapping and discovery, and Sol/medium for selected details and positive
architecture/CI finding review. Existing explicit model choices are preserved. Global ordered
review passes intentionally replace dedicated finding reviewers; the effective policy is shown
in settings and run status. A blank timeout means unlimited waiting, while a finite value sets a
per-call deadline. Inspect measured attempts and failures before changing models or concurrency.

## Instructions for your project's agents

Adapt this block for the target repository's `AGENTS.md` or equivalent instructions:

```text
Use AnaxiGraph for substantial code changes:
- Scan this checkout and prepare the task before editing. Keep the original baseline.
- Inspect likely owners, public contracts and relevant saved recommendations. Reuse existing capabilities.
- Assess substantial plans. Record adopt/defer/reject reasons for material architecture findings.
- Keep components cohesive, public interfaces small, implementation details private and tests independently runnable.
- After editing, scan, execute the appropriate project checks, then validate against the original baseline.
- Review Patterns after meaningful features/refactors. Use Fresh Eyes at milestones and regularly during active development.
- Revisit CI/CD when architecture or shared build inputs change. Use declared check inputs; widen for contracts, consumers and unresolved impact.
- Turn accepted recommendations into concrete tasks. Report what was changed, deferred or rejected and why.
- Report whether AnaxiGraph helped: which advice you used, what it changed, wasted output/calls, and anything important it missed.
```

Treat cadence as a team convention. You can ask an agent to conduct a review and implement selected
improvements in one task. It should inspect source, test the change and report the actual result.

## CI/CD and portable records

The CI/CD review reads pipeline declarations and proposes improvements. To use deterministic
selection, author `anaxigraph/ci-policy.json` and connect its `ci-plan` output to your runner.
The [policy reference](change-request-ci-delivery.md#declare-accepted-tasks) explains task inputs,
ordering, consumed outputs, fallbacks, cache keys and release/scheduled checks.

CI reviews capture bounded literal runner/selector bodies and local Python script imports, including
their working directories. Redaction, missing scripts and capture limits remain explicit. To supply
observed test-selection evidence, use the separate experimental collector from this checkout:

```bash
uv run python experiments/feature_drift/collect_evidence.py \
  --repo /path/to/repo --output /path/to/repo/anaxigraph/ci-evidence.json
```

The collector explicitly runs that repository's `.venv` tests and selector from `ci.json`; its test
environment needs pytest-cov. The AnaxiGraph service never runs project tests. The canonical report
refreshes atomically after success; other output paths must be new. Add `--measure-diff --changed path`
(repeat `--changed` for the complete diff) to time full and selected checks on the same source.

After scanning, CI review, `ci_plan` and change validation expose omissions from this versioned
report. It contains exact input fingerprints, environment fingerprints, one test-ID inventory,
and per-source executing/selected inventory indices. It excludes itself from input identity.
Pass matching `runtime_context` to `ci_plan` when comparing environments. Stale/invalid reports,
unobserved inputs, omitted consumers and environment uncertainty require review before using that
report to narrow checks. Declared policies without reports keep their separate policy basis.
Contract substitutions remain supplied claims; sampled fault success does not erase coverage gaps.

Commit `anaxigraph/record.json` after full workflows or explicit export. A nightly cloud job can
pull a repo, run its selected reviews, export and commit the record. Another clone scans locally
and reuses analyses whose inputs still match. Mapping, exporting, and committing/pushing are
separate operations. Ignore `.anaxigraph-v2/`, which contains local indexes, progress and trial data.

## Learning from real use

The [audit follow-up](audit-followup-2026-10-27.md) records current fixes and earlier trial measurements.

Periodically ask coding agents what they actually consulted, which recommendations changed their
implementation, which were rejected, and what the tool failed to find. Ask for examples and source
references rather than general impressions. Check whether the next change was easier, whether
required checks stayed useful, and whether CI feedback got faster without missed regressions.

The local evidence ledger distinguishes supplied context, inspected entities, model usage and
reported outcomes. `anaxigraph evidence --repo /absolute/path/to/your/repo` reads it; add `--export` to save a local report.
Retention is bounded and reports dropped history. Missing provider prices stay unknown.
Use this alongside agent interviews and project outcomes; inspection alone does not show that an
agent acted on advice.

For a controlled comparison, use the [architectural outcome experiment](../experiments/architecture_quality/README.md).
Its discovery track starts from identical coupled code and natural feature requests, freezes
Patterns/Fresh Eyes advice, then gives fresh agents unannounced later changes. Independent checks
verify behavior, persisted-state compatibility and safe CI scope; blind patch review judges the
chosen boundaries and contracts. A separate guided track checks prescribed component isolation.
Ordinary coding and a generic architecture-review control are supported. Tokens, latency and
additional analysis work remain separate from quality.

For longer integration and CI/CD histories, use the
[integration drift benchmark](../experiments/drift/README.md). It carries earlier code and state
into later tasks, separates safety from work avoided, and includes faults that an unsafe selector
must catch. Future coding launches default to **GPT-6.1 Sol / high**. Product analysis profiles and
previously frozen experiment results retain their separately recorded settings.

`prepare_change` needs a structural scan, not a full model mapping. Its three owner candidates
combine source text, paths/symbols and current saved understanding, with matching evidence.
Historical associations show files that changed together before the task baseline; inspect them
for shared obligations or weak boundaries without assuming they must change or widen CI.
Workflow status retains compact failure diagnostics after older progress files rotate away.

## Connections

Use explicit repository paths for stdio MCP. A dashboard service also exposes HTTP MCP at `/mcp`.
Test an actual tool call after configuring client permissions; attaching the server does not prove
the client permits unattended calls.

Default `serve` stays on `127.0.0.1:8766` and needs no authentication setup. Local browser requests
must have a trusted Host and matching Origin. For remote use, prefer an SSH tunnel to that loopback
service, such as `ssh -L 18766:127.0.0.1:8766 user@server`; open `http://127.0.0.1:18766` locally.

For a shared service behind an HTTPS reverse proxy, explicitly trust its public URL and configure
a random token of at least 32 characters:

```bash
export ANAXIGRAPH_HTTP_TOKEN="$(uv run --no-project python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uvx --from anaxigraph==1.0.0 anaxigraph serve --repo /path/to/repo --host 0.0.0.0 \
  --allow-origin https://anaxigraph.example.com
```

Replace the example hostname with the proxy's exact scheme, hostname and port; paths and wildcards
are not origin patterns. The proxy must preserve the public Host and HTTPS scheme. Repeat
`--allow-origin` for additional trusted server URLs. Store the token in the service's protected
environment, outside Git and URLs. Token authentication does not encrypt HTTP; use HTTPS or an
encrypted tunnel for remote traffic.

The browser prompts for HTTP Basic authentication: username **`anaxigraph`**, password **the token**.
API and HTTP MCP clients can instead send `Authorization: Bearer TOKEN` to the public URL, including
`https://anaxigraph.example.com/mcp`. Authentication and Host/Origin checks cover the dashboard,
REST API and MCP; binding to a LAN address without a token and allowed origin is refused.
