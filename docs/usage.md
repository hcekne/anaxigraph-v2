# Using AnaxiGraph during development

AnaxiGraph is most useful when its understanding influences a real change. Use it to find the
right owner, reuse existing behavior, evaluate a smaller public interface, and choose the checks
that belong to that change. Run deeper reviews regularly enough to challenge accumulated design
choices, then give their findings to the coding agent as concrete work.

## Everyday agent loop

1. **Scan** the exact checkout. A structural scan uses no model calls. If current semantic
   understanding is missing, run Mapping; compatible unchanged understanding is reused.
2. **Prepare** a substantial task with `prepare_change`. Keep its baseline and task identity.
   The compact reply names likely owners, contracts, test links and relevant saved recommendations.
   Use `inspect` to expand a module or saved review; `inspect("charter")` returns the general principles.
3. **Assess the plan** before a substantial edit. `assess_plan` continues the prepared task even
   when its description is reworded. Adopt, defer or reject relevant material architecture findings
   with a reason. An accepted recommendation is a decision to do work, not evidence that it is done.
4. **Implement and check** the change. Prefer local behavior and public-contract tests; include
   consumers when contracts, behavior, shared resources or configuration can affect them. Keep the
   project's required checks.
5. **Scan and validate** the candidate against the original baseline. Supply check commands,
   exit codes and the candidate snapshot ID. Explain unresolved findings or missing checks.

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
reported outcomes. `anaxigraph evidence` reads it; `anaxigraph evidence --export` saves a local report.
Retention is bounded and reports dropped history. Missing provider prices stay unknown.
Use this alongside agent interviews and project outcomes; inspection alone does not show that an
agent acted on advice.

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
export ANAXIGRAPH_HTTP_TOKEN="$(uv run python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run anaxigraph serve --repo /path/to/repo --host 0.0.0.0 \
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
