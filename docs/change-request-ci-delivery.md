# CI/CD as architectural policy — 27 October 2026

## The problem and intended behavior

A component is an independent unit of development and verification only when the delivery pipeline
can use that independence. The maintainer reported a payments fix with a 16-second focused test
followed by more than 61 minutes of CI. Those numbers are a user-reported example, not an AnaxiGraph
benchmark. A separate read-only CI investigation recorded one Python job lasting 2,041 seconds;
that job duration is not total pipeline wall time or a measured opportunity for equivalent savings.
The inspected pipeline already had browser selection. Existing optimizations must be recognized
before proposing replacements. No pipeline in that application was changed.

AnaxiGraph should help an agent answer: which local checks establish this component's behavior,
which public or shared agreements could widen the work, and what is the shortest justified path to
shipping? A kitchen change should usually have a kitchen-sized feedback loop. A shared roof change
must reach the rooms that actually depend on it. Directory names and unchanged signatures alone
do not establish either case.

This implementation connects that intent to two capabilities: an evidence-based **CI/CD review**
workflow and a deterministic **`ci-plan`** operation over accepted repository task declarations.
It adds no project commands, deployments or approval gates to normal mapping.

## Review the actual delivery design

```bash
uv run anaxigraph run ci --repo /path/to/repo
# Optional: review one pipeline using the same workflow engine.
uv run anaxigraph run ci --repo /path/to/repo --scope .github/workflows/ci.yml
```

**CI/CD** is the fifth dashboard workflow. CLI `delivery` is an alias for `ci`; individual requests
use kind `delivery`. It works without mapped Python modules or semantic annotations. Every new
Pattern run also includes one root CI/CD review alongside the three component scouts and pattern
screening. Old saved runs retain their original protocol.

The dedicated worker receives bounded declarations: GitHub Actions triggers, jobs, `needs`,
conditions, matrices, commands, working directories, concurrency and cache settings. Local reusable
workflows and composite actions are followed within the fingerprinted checkout. Root package-script
commands and literal local script paths/fingerprints expose existing selectors without executing
or copying their bodies. Remote actions/workflows and dynamic expressions stay opaque. Environment
values, credential-related text and URLs are omitted from command previews.

Accepted CI policy and inferred top-level source areas accompany those facts. Inferred areas are
not accepted component boundaries. Public-interface/test-topology identity makes structural design
changes visible without refreshing the review for every private Python body edit. CI, package,
selector or accepted-policy changes invalidate their corresponding evidence. Reusing advice never
reuses a test result.

The default draft worker is **Luna/low**. A draft with positive recommendations receives a separate
**Sol/medium** review; keep/unknown-only drafts make no extra strong-model call. Configure `delivery`
and `delivery-review` stages, fallbacks and explicit ordered review passes through the shared
workflow settings. Saved work, resume, attempts, elapsed times and final reconciliation use the
existing engine.

Each captured pipeline receives exactly one finding/keep/unknown assessment. At most three
recommendations identify:

- The observed bottleneck and a concrete fast path.
- Exact widening conditions, including contracts, consumers, schemas, fixtures and shared inputs.
- Cache keys, invalidation, safe reuse and missing runtime assumptions.
- Parallelism, cancellation and deployment/consumer checks.
- Required checks to retain and practical next actions.
- Source evidence and uncertainty, including missing duration measurements.

Positive recommendations cite every named pipeline within captured line bounds. Strong review
challenges existing optimizations and unsupported claims. This validates coverage and provenance;
it does not prove recommendation quality, equivalent assurance or executed checks.

Capture limits are explicit: up to 16 primary configurations, eight local references, 64 KiB source
and projected metadata per configuration, 30 jobs and 20 steps per job. Nested values, scripts,
policy targets and component projections are bounded too. Truncation or unsupported declarations
remain visible as partial evidence; select narrower pipeline scopes when needed. Other detected CI
formats have metadata-only unsupported status. Fresh Eyes remains behavior-only and never receives
this source-aware CI context.

## Declare accepted tasks

Track **`anaxigraph/ci-policy.json`** with source. Its version-1 schema accepts `targets`, `always`,
`fallback`, `shared_inputs` and `ignored_inputs`. Example commands below are placeholders for targets
you have actually established; AnaxiGraph will not run them.

```json
{
  "version": 1,
  "targets": [
    {
      "id": "payments",
      "command": "uv run pytest tests/payments",
      "inputs": ["packages/payments/**", "tests/payments/**"],
      "cache": true
    },
    {
      "id": "payment-consumers",
      "command": "uv run pytest tests/contracts/payments",
      "inputs": ["packages/payments/api.py", "contracts/payment.json", "tests/contracts/payments/**"]
    },
    {
      "id": "full-regression",
      "command": "uv run pytest",
      "inputs": ["packages/**", "app/**", "contracts/**", "tests/**"],
      "cadence": "scheduled"
    }
  ],
  "always": ["payment-consumers"],
  "fallback": ["full-regression"],
  "shared_inputs": ["uv.lock", "pyproject.toml", ".github/workflows/**", "anaxigraph/ci-policy.json"],
  "ignored_inputs": ["README.md", "docs/**"]
}
```

| Target field | Meaning |
| --- | --- |
| `id`, `command` | Stable task identity and declared command. |
| `working_directory` | Repository-relative directory; default `.`. Input paths remain repository-relative. |
| `inputs` | Actual source, tests, fixtures, contracts and configuration read by the task. Required unless the task is explicitly `always` retained. |
| `needs` | Prerequisite ordering. An upstream edit alone does not invalidate an ordering-only follower. |
| `consumes` | Data/output dependency: selects prerequisites and propagates producer changes to consumers. |
| `cadence` | `change` by default; `release` or `scheduled` tasks remain distinct obligations. |
| `kind` | `test` by default, or `build`, `check`, `deploy`. Deployment is never cacheable. |
| `cache` | Opt into calculating a declared-input cache key; default false. |
| `estimated_seconds` | Optional finite estimate, not an observed duration. Omit when unknown. |

Globs use POSIX segments: `*` stays within one segment; `**` spans zero or more segments. Shared
inputs affect the relevant task scope. Explicit `always` targets remain selected, with prerequisites.
Explicit `--cadence scheduled` or `--cadence release` retains that phase's obligations even without
source edits; an ordinary change plan reports those separate obligations without silently dropping them.
Unknown ownership selects only the authored fallback and remains incomplete; it does not quietly
run every deploy or claim that a broad fallback proved coverage. Ignored paths must have no declared
task owner; ignores cannot override actual inputs, including a broad scheduled target's inputs.
Declare fallback inputs accurately rather than assuming `**` permits documentation-only ignores.

## Preview and adopt a change plan

```bash
uv run anaxigraph scan --repo /path/to/repo
uv run anaxigraph ci-plan --repo /path/to/repo --base-ref main
```

Alternatively select `--base-snapshot-id`, use a pinned `prepare_change` baseline, or supply
`--changed-paths paths.json` containing a JSON array. Choose one comparison source. A Git comparison
identifies the resolved local base commit and includes eligible untracked paths; snapshot comparisons
identify their baseline. Renamed/deleted paths remain relevant to selection.

The result supplies selected target commands, prerequisite layers, widening reasons, uncovered and
ignored paths, retained periodic/release checks and whether architectural CI re-evaluation is
required. An incomplete plan remains incomplete even when it includes a fallback command. Review
public agreements, shared resources and unsupported relationships before adopting narrower scope.

For optional key calculation, pass `--runtime-context runtime.json` with the relevant runner,
toolchain, environment/service and other external input fingerprints. A nonempty object is a caller
declaration, not proof of completeness. Keys include matched source inputs, target command/directory,
shared inputs and consumed producer keys. Missing/deleted inputs, unknown producer keys or incomplete
scope disable cache eligibility. AnaxiGraph queries no result cache and reports no cache hits. The
runner must establish task purity and complete declared inputs before trusting reuse.

Use the plan in your existing task runner: retain required status reporting, run selected checks in
dependency order and preserve the project's release, security and integration obligations. The
planner does not rewrite a workflow, execute a command, cancel jobs or deploy. Required GitHub checks
can remain pending when a whole workflow is skipped; selection must preserve a reporting path.
[GitHub required-check behavior](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).

Re-evaluate delivery design with architectural revisions, changed public contracts, shared inputs,
CI configuration or unresolved ownership. Both `prepare_change` and `assess_plan` expose compatible
saved CI advice; test selection comes from authored task inputs, never the two-level source map.

## Qualification and limits

Historical mapping/Pattern timings in earlier change requests predate this root CI/CD review. They
are not measurements of this expanded protocol. New qualification must distinguish deterministic
selection/serialization tests, synthetic workflow contracts and actual model-reviewed advice.
Measure focused feedback time, critical-path wall time, retained obligations and missed regressions
before claiming faster delivery with equivalent assurance. Declared duration estimates produce an
idealized dependency-path lower bound; missing measurements and savings remain unknown.
