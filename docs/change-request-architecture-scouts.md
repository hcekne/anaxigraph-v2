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

## Qualification — 27 October 2026

The first live pass used `c8b6279`: actual Luna/low workers, a 32-worker pool and no elapsed model
deadline on two small synthetic examples. The private expectations stayed outside the analysis
roots and were not supplied to workers. One example couples shared behavior and persistence;
the other already contains an isolated importable library with declared package metadata.
Exact versions, wheel hashes, counts and limitations are recorded in
[the public qualification evidence](evidence/architecture-scout-qualification.json).

| Initial live measurement | Coupled example | Already isolated example |
| --- | ---: | ---: |
| Current mapped annotations | 3 | 5 |
| Production modules assessed by each lens | 2 | 4 |
| Declared module/lens assessments | 6 | 12 |
| Mapping wall time | 11.9 s | 14.6 s |
| Scout workflow wall time | 19.5 s | 14.1 s |
| Unchanged scout repeat | 0.11 s, zero provider calls | 0.19 s, zero provider calls |
| Saved canonical scout-result JSON | 12,554 bytes | 14,971 bytes |
| Portable record | 24,318 bytes | 32,909 bytes |

Across both examples, **17 actual provider calls produced 17 accepted analyses**: eight mapping
calls and nine scout calls, with no fallback attempts. Nine separate receipt events recorded reuse;
they were not additional paid calls. Provider receipts report token usage; dollar cost is unknown.
These fixtures and model-only scout workflows are not a benchmark of full application development.

The positive example surfaced useful public behavior and data-ownership seams and proposed checks
that avoid initializing persistence. Some advice still combined pure calculation with persistence,
and a proposed comparison check already existed. The negative result was **partial, not a pass**:
the package worker recommended a material workspace distribution despite seeing existing declared
distribution metadata, and the verification worker proposed checks that were already present.
Public re-exports remain unresolved in the static graph, so missing consumer/test links contributed
to those judgments. Empty observed test edges do not establish missing tests or missing isolation.

That observed failure led to `cead5d1`: scouts must establish the existing boundary before proposing
a concrete incremental improvement. Positive drafts receive a separate stronger review; the policy
is part of the request fingerprint, and actual executed reviewers remain distinct from configured
reviewers. Keep/unknown-only drafts avoid the extra call. No fixture-specific component detector
or expected answer was added. The original false-positive results remain available as evidence.
The follow-up used the same source and all eight saved annotations. It retained four useful
opportunities in the coupled example, all at optional priority 1: a public pricing operation,
application-owned data retrieval and a possible local importable pricing capability. The isolated
library's three modules received `keep` under all three lenses (nine assessments); its example
consumer received `unknown` under all three (three assessments). No packaging move or duplicate
test suite was recommended for the core library in that run. Unresolved re-export/test evidence
remains a real limitation; restraint on one example does not establish complete understanding.

| Reviewed scout measurement | Coupled example | Already isolated example |
| --- | ---: | ---: |
| Mapping annotations reused | 3 | 5 |
| Scout workflow wall time | 70.82 s | 35.83 s |
| Actual calls | 3 Luna scouts + 3 Sol finding reviews | 6 Luna scouts + 1 Sol validation fallback |
| Accepted final scout analyses | 3 | 6 |
| Findings | 4, all optional | 0 |
| Unchanged scout repeat | 0.32 s, zero provider calls | 0.60 s, zero provider calls |
| Saved canonical scout-result JSON | 16,339 bytes | 6,094 bytes |
| Portable record | 28,103 bytes | 24,032 bytes |

The reviewed round made **13 actual calls and accepted nine final analyses**. One Luna answer was
rejected for an invalid uncertainty-text field; its configured Sol/medium fallback returned valid
`keep` outcomes. There were no provider transport failures. Three other Sol calls reviewed positive
drafts; they were not fallback calls. The stronger checks add latency. The prompt and review policy
changed together, so this is descriptive qualification rather than a causal prompt experiment or
proof of general architectural usefulness. Mapping was warm reuse, not a new cold timing.

Real accepted findings were then delivered through CLI and five successful software-client MCP
calls. An ordinary task received a relevant scout proposal, material proposals required decisions,
and three deferrals with reasons were recorded. Subsequent validation kept implementation
**unverified** and behavior **unchecked**. A new task on the same snapshot did not inherit the old
plan. This exercised tool delivery and accounting without an LLM coding caller or new model calls.
The reviewed findings were also delivered through five actual software-client MCP calls. All were
optional, so that fixture correctly reported no current material proposals and required no
dispositions; ratings were not raised to manufacture a plan gate. Implementation remained
unverified. The initial real-result gate exercise and current synthetic gate tests are separate
evidence.

Different-path filesystem copies imported the actual accepted records: three annotations and
three scout analyses for the coupled example, five annotations and six scout analyses for the
isolated example. All three lenses reused their results with zero provider calls. Three repeated
scan/import/export cycles preserved record bytes and SQLite sizes; input identities and original
provenance survived. This proves local record portability and stability, not a remote GitHub
round trip. Fresh Eyes was not included in these records or rerun by this qualification.
Copies of the reviewed records also preserved all three/six analyses and their reviewer provenance.
They reused in 0.2/0.3 seconds with zero provider calls and again stayed byte- and size-stable across
three scan/import/export cycles.

Separate browser checks exercised four workflow cards, saved contracts/options/check proposals,
explicit plan decisions and desktop/mobile layouts without overflow or browser errors. An
installed-wheel MCP software client completed ten calls without failures and reused three accepted
synthetic scout batches. These transport/UI checks used synthetic preparations and made no paid
model calls; they do not establish recommendation quality.
A second browser check on `cead5d1` exercised the configured Sol reviewer: two synthetic annotations,
three Luna scout callbacks and one Sol callback for the positive draft. Keep/unknown callbacks
skipped the reviewer. Decision controls and 1440/390-pixel layouts worked without browser errors.

Actual GitHub CI passed for [initial `c8b6279`](https://github.com/hcekne/anaxigraph-v2/actions/runs/40526659840)
and [reviewed `cead5d1`](https://github.com/hcekne/anaxigraph-v2/actions/runs/40529143859). The latter
passed **468 core tests plus 29 harness tests on both Python 3.11 and 3.12**, Ruff, mypy, source/wheel
builds and installed-wheel checks. The reviewed build contains 10,117 production physical lines;
the final guidance-only update contains **10,147**, an increase of **1,080** from 9,067. The revised
ceiling is 10,150, with no new complexity allowance. Existing scheduling,
providers, storage, portable records and timing machinery were reused.

### Full current workflow on Snow Rankings

An isolated copy of Snow Rankings at `af50630`, with all 34 source fingerprints matching the
preserved checkout, ran a genuine cold map and the complete new Pattern workflow on `cead5d1`.
It mapped twelve Python modules into twelve current annotations, then reviewed six production
modules. Five tests and one inert marker stayed mapped but outside the default Pattern target
scope. Non-Python resources were fingerprinted, not parsed.

- Cold mapping: **50.2 seconds**; full Patterns including the scouts: **57.0 seconds**;
  combined **107.2 seconds**.
- All 133 cards were exposed to screening for six modules: **798 exposed pairs, 38 selected and
  rated**. Exposure is not an individual rating or proof of irrelevance for unselected cards.
- Three scout jobs accounted for all **18 module/lens pairs**, returning `keep` for all eighteen.
  No positive-draft judge call was needed. This is a scoped outcome, not proof the application has
  no architectural improvement opportunities.
- The unchanged workflow reused all fifteen screen/detail/scout batches in **1.1 seconds**, with
  zero new model calls and unchanged canonical review counts.
- One portable file stayed byte-identical at **109,914 bytes**. Saved minified results contained
  3,148 bytes of screening, 28,116 of detailed Patterns and 2,712 of scouts.

All **28 provider calls returned**: thirteen for mapping, six for screening, three for scouts and
six for detailed reviews. One Luna mapping answer failed its producer-text contract and the
configured Sol/medium fallback supplied the accepted result; the final accounting is 27 accepted
analyses and one rejected answer. Pattern and unchanged runs recorded no failed attempts.
The measurement harness corrected its batch-pagination call after cold mapping had already
finished, then read the saved completed result without repeating accepted work.

This is a larger workload than the earlier 83.1-second mapping/Patterns sample, so those times are
not directly comparable. No Fresh Eyes, controlled edit cycle, target tests or source changes were
performed in this run. Optional clock-input and projection-isolation suggestions had modest costs
and potential benefits; this is not evidence of a material architecture improvement. The original
Snow checkout and portable record remained unchanged.

Local deployment of final `5d461e3` was checked separately. Both idle AnaxiGraph services ran source
matching the recorded installed wheel. Existing settings and annotations were preserved, with the two new
model choices added; the original Snow record stayed unchanged and its preview remained active.
Deployment itself started no model jobs. This updates source and local runtimes; no PyPI release
was published.
The final code's [actual CI run](https://github.com/hcekne/anaxigraph-v2/actions/runs/40531055123)
also passed. This update compacts default scout previews and preserves an owner locator; model
packets and pipelines remain those qualified at `cead5d1`.

The final installed runtime rebuilt all nine reviewed scout input identities unchanged and reused
both actual-result workflows with a provider configured to fail if invoked: zero model calls,
unchanged accepted results and preserved earlier qualification artifacts. A final CLI/MCP check
received a **6,982-byte default brief** retaining two owner entries, affected paths, an observed
test link and compact scout advice. Four software-client MCP calls passed; expanded package
reviews still retained all four placement comparisons. Current optional proposals remained
advisory, and implementation remained unverified. These checks bind the final guidance update to
the measured model results without claiming another live model evaluation.
