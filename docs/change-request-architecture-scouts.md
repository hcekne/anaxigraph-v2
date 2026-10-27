# From architectural intent to review work — 27 October 2026

## Decision

A charter alone does not make a coding agent apply it. AnaxiGraph must schedule focused searches,
validate their output, deliver findings to the coding task and record an explicit response.

An independent component can be a standalone imported library in another repository. In-process
communication does not require keeping source in one repository. Repository, package, process and
deployment boundaries are separate decisions. The maintainer's Temporal example motivates this:
a consumer uses a dependency's contract and compatibility constraints without maintaining its
internals. A project-owned library can offer the same separation. No Temporal-specific detector
or mandatory repository split is added.

Use the existing application, provider runner and bounded storage. Retain catalogue reviews and
blind Fresh Eyes. Do not create a second agent framework or automatically turn proposals into CI rules.

## Three actual single-purpose workers

| Lens | Assigned search |
| --- | --- |
| `boundaries` | Find responsibilities that should evolve behind smaller public contracts, private implementation access and leaked provider/storage details. |
| `packages` | Find cohesive capabilities suitable for standalone libraries. Compare staying in place, a local package, a versioned workspace distribution and a package in another repository. Include ownership, consumers, dependency burden and release coordination. |
| `verification` | Find smaller behavior/contract test contexts. Identify shared state, infrastructure and missing evidence that could extend the verification scope. |

Each lens is a separate provider job, not three headings in one generic review. Defaults use
Luna/low, then Sol/medium after provider or validation failure. A positive scout draft also receives
a focused Sol/medium review before acceptance; keep/unknown-only drafts skip that call. This second
worker challenges the incremental benefit and evidence, removing redundant boundaries or claims
based only on missing test edges. Explicit ordered review passes replace the default reviewer.
Model choices and unlimited timeout settings remain configurable. Review is still a model judgment,
not proof of correctness. Its policy is fingerprinted with the request, while execution provenance
distinguishes configured reviewers from those actually called. Legacy saved runs retain their
configured review pipeline.

Groups contain up to eight production modules, provisionally grouped by directory, with bounded
neighbor behavior, public surfaces, observed test links and source relationships. These scheduling
groups are not accepted components. The model may challenge them. Findings must identify members
inside the reviewed scope; follow-up work can name neighboring dependencies without claiming they
were reviewed as members. Tests are context by default, not targets for every lens.

Packets include bounded, fingerprint-checked declarations from root `pyproject.toml`/`package.json`:
name/version, dependency constraints, workspace members, script names and test paths where present.
These do not prove publication, installed versions, external implementation behavior or nested
package state. Script bodies and direct dependency URLs are omitted. Missing, invalid or oversized
metadata is unknown. An unresolved graph node is not proof of an installed dependency.

## Executable result requirements

Every scoped module needs exactly one `finding`, `keep` or `unknown` outcome. Unknown outcomes
require a brief explanation of missing evidence. No-change outcomes get no rejection essay. There
is no extraction quota; a batch may return zero findings and may never return more than three.

Each finding requires member IDs, priority, proposed placement, a public behavioral contract,
private responsibilities, dependencies, proposed verification, a concrete next step, alternatives
with benefits/costs and source evidence for every member. Package findings must compare all four
local/workspace/repository choices. Services are an additional option requiring a deployment,
isolation or scaling benefit. Priority 1 is optional, 2 material and 3 high-priority; these are judgments.

Validation rejects missing/duplicate module outcomes, wrong lenses, unsupported placements,
incoherent membership, absent comparisons, empty contracts and invalid references. Final
reconciliation requires every declared module/lens pair exactly once. Missing a worker cannot
produce a completed review. Reference validity does not prove that the cited source supports the
argument, so recommendations remain inspectable proposals.

## Workflow integration

New Pattern runs include all three scouts automatically, bypassing catalogue shortlisting. An
omitted pattern therefore cannot suppress the package search. Scouts and catalogue screening use
the same worker pool; selected detailed reviews follow. The separate **Component Architecture**
workflow runs these searches without the catalogue matrix:

```bash
uv run anaxigraph run architecture --repo /path/to/repo
uv run anaxigraph run patterns --repo /path/to/repo
uv run anaxigraph request architecture --repo /path/to/repo --lens packages --scope src/example.py
```

CLI, MCP, HTTP and the fourth dashboard card expose the same operations and per-lens timings.
Accepted jobs save immediately and resume retries only unfinished work. Compatible unchanged
inputs and model pipelines reuse results with no provider calls. Lens participates in request,
local retention and portable retention identities, so three results cannot overwrite each other.
Older durable runs resume their recorded protocol without retroactively changing coverage.
Mapping remains concise mapping; Fresh Eyes remains independently requested and source-blind.

## From finding to coding decision

`prepare_change` supplies current owner-scoped architectural findings for ordinary coding tasks,
not only tasks mentioning architecture. It reports partial/missing scout coverage. Default briefs
stay bounded and expand through review IDs; architecture has a dedicated query so catalogue screens
cannot crowd it out.

`assess_plan` checks the named files as well as candidate owners. Its `architecture.requirements`
contains up to twelve current material proposals. Each requires `adopt`, `defer` or `reject` with
a short reason. Missing dispositions yield `decision_required`; duplicate/unknown IDs and malformed
decisions are rejected. Overflow yields `scope_incomplete`. Rejection and deferral are valid: the
requirement is to make a decision, not to agree with the model.

```json
[
  {
    "proposal_id": "ID_FROM_ARCHITECTURE_REQUIREMENTS",
    "decision": "defer",
    "reason": "The existing public contract is sufficient for this fix; revisit before adding another consumer."
  }
]
```

```bash
uv run anaxigraph assess "Your task" --repo /path/to/repo --files src/example.py --decisions decisions.json
```

MCP `assess_plan` and HTTP `/api/assess` accept that optional `decisions` array. The dashboard offers
the same choices and reason inputs. Decisions are recorded in the existing bounded local task
ledger, scoped to the active task and pinned baseline. They are not portable accepted project rules.
Disabled evidence collection is reported as not recorded.

`validate_change` exposes the retained dispositions for the matching task/baseline, with implementation
status **unverified**. Adopted intent and a supplied passing test do not establish implementation.
A new task or mismatching baseline cannot inherit old apparent follow-through.

## Costs and limits

The production allowance increases from 9,100 to 10,150 physical lines for scoped contracts and
validators, package declarations, shared-runner orchestration, identity handling and plan/UI
follow-through. Existing provider execution, SQLite, export/import, retries and timings are reused.
The final 50-line allowance covers conditional strong review after a real negative control
exposed a redundant package recommendation from the cheap scout.
No complexity ceiling is raised. Tests and experiment code remain separately reported.
The earlier 83.1-second mapping/Patterns measurement predates the mandatory scouts; it is not a
measurement of this larger workload.

Directory groups and twelve neighbors can miss relationships. Verification workers receive
summaries, not the complete test/CI implementation. The existing two-level impact neighborhood is
not a complete CI scope. This does not automatically extract code into repos, audit external
package internals or authorize skipping required tests.

Enforcement here means actual targeted jobs, mandatory coverage/output, delivery into planning and
explicit dispositions. A coding harness must use the tools and honor their statuses; AnaxiGraph
cannot stop an unrelated agent from bypassing them. Hard enforcement of an implementation requires
accepted executable project contracts: allowed dependencies/public APIs, resource ownership and
behavior/contract checks in CI. Model proposals do not silently become those rules. Current private
access/cycle findings remain partial evidence, not a complete component-contract engine.

Qualify with coupled capabilities, an already isolated package, missing/malformed lens output,
explicit dispositions, interrupted runs, portable clones and actual UI/MCP delivery. Retain false
positives and misses. Synthetic shape tests do not establish live recommendation quality.
