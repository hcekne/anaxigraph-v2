# Type and size regression checks

CI runs `uv run mypy` across production Python and the quality-check script. It checks untyped
function bodies too, without ignored-error or missing-import exemptions. This catches inconsistent
record shapes, nullable values used without checking and incorrect component calls; it does not
establish runtime or architectural correctness.

`uv run python scripts/quality.py` reports production, tests, experiment harness and tooling sizes
separately. The production budget counts every physical Python, JavaScript, CSS and HTML line under
`src/anaxigraph`, including comments, blank lines, docstrings and model instructions. Package JSON
data is reported separately and has its own byte budget. Tests and experiment code are visible in
the report rather than folded into the production count or omitted from the accounting.

The explicit limits in `quality-budget.json` are 12,050 production lines, 800 lines per
production file and 200,000 bytes of package JSON data. New Python functions have a decision-score
ceiling of 15. Existing functions above that value have named limits equal to their measured debt,
so they cannot silently become more complex. The score is one plus branch, boolean and comprehension
decisions in each function. Nested functions are measured independently; registering many small
route handlers does not charge all their branches to the registration function.

The initial debt values are pinned to commit `dcc1a68`, rather than regenerated from each change.
Two reviewed CLI limits explicitly permit the new opt-in publication and evidence commands: their
flat registration and dispatch remain clearer than extra forwarding helpers created for a score.
Reviewed limits carry a concrete explanation in the configuration; the checker rejects an unexplained
exception.

The production allowance increased from 7,600 to 7,700 for explicit unlimited model-call timing,
per-worker Codex reasoning choices and honest evidence of those choices. The dashboard preserves
these settings, rather than turning a blank deadline into zero or dropping a configured reasoning
effort. Timeout validation has one reviewed optional-value branch; finite timeouts remain checked.
Another 50 lines permit observed inert-module classification, honest empty capability lists and
Fresh Eyes coverage that preserves marker modules without fabricating functional requirements.
A further 100 lines enforce the same source-identity boundary on saved capability and contract
statements, selectively refresh leaking legacy briefs and reuse compiled matchers across annotations.
One reviewed portable-import branch preserves safe entries when another brief requires correction.
Another 50 lines record a bounded resource-context identity for portable analyses, so unrelated
documentation changes can reuse exactly matching scoped inputs while changed opaque resources or
cited obligations still invalidate them. The import guard has one reviewed compatibility branch.
Another 100 lines retain bounded workflow attempt failures and distinguish successful provider
transport from accepted analysis. Each batch keeps three sanitized failure details plus its cumulative
count; status reports retries even after success, while older missing failure history stays unknown.
The 27 October focused-review change adds a 1,000-line allowance for the ten-principle charter,
public-surface observations, two-pass selection/validation, indexed scoped reuse, batch timings and
shared UI/API controls. The exhaustive compatibility path remains explicit; no second application
or provider framework is introduced. New helpers keep their decision score at or below 15.
A further 100-line allowance keeps changing default shortlists bounded to one current result per
module, preserves explicit selected/exhaustive shards, and removes inferred-neighbor wording from
cache-critical context. Existing save, import and portable-serialization functions have reviewed one-branch metadata cases.
The next 1,000-line allowance implements mandatory architectural scouts, declared root package
facts, explicit plan dispositions and the shared UI/API. Existing execution and persistence are
reused; no function-complexity ceiling is raised. See the
[scout decision](change-request-architecture-scouts.md) for behavior and enforcement limits.
Another 50 lines permit conditional strong review of positive scout findings. A live negative
control exposed a redundant packaging recommendation, so positives now need a second judgment;
keep/unknown outcomes skip that call. This reuses the existing draft-review pipeline and records
configured policy separately from reviewers actually called.

The CI/CD change adds a 1,900-line allowance for bounded, redacted workflow evidence capture,
a separate delivery specialist, and deterministic declared-input check selection with cache
fingerprints, dependency ordering, explicit baselines, portable retention and shared UI/API access.
This is a new capability, rather than a second implementation of mapping or model execution.
It reuses the existing resumable workers and positive-finding review. No function-complexity or
per-file limit increases. The allowance includes JSON validation, non-executing YAML parsing and
honest incomplete-scope reporting; no project tests or deployment commands are executed by the planner.

When a check fails, simplify the change or deliberately revise the relevant budget with an
explanation of the required behavior and tradeoff. CI never rewrites the baseline or raises a limit.
These checks run in CI; they do not add pre-push gates. Necessary product changes can still grow
the project, but that growth and retained complexity must be an explicit decision.

The separate Snow Rankings acceptance and experiment-harness tests also run in CI without making
model calls. Actual coding outcomes, regressions and ease of the subsequent change remain the
evidence for whether AnaxiGraph helps; line counts and type checks are supporting constraints.
