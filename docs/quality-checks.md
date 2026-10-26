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

The explicit limits in `quality-budget.json` are 7,700 production lines, 800 lines per
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

When a check fails, simplify the change or deliberately revise the relevant budget with an
explanation of the required behavior and tradeoff. CI never rewrites the baseline or raises a limit.
These checks run in CI; they do not add pre-push gates. Necessary product changes can still grow
the project, but that growth and retained complexity must be an explicit decision.

The separate Snow Rankings acceptance and experiment-harness tests also run in CI without making
model calls. Actual coding outcomes, regressions and ease of the subsequent change remain the
evidence for whether AnaxiGraph helps; line counts and type checks are supporting constraints.
