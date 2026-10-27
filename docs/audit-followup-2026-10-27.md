# Audit follow-up — 27 October 2026

Seven real bugs were fixed:

1. Enforce trusted Host/Origin checks and authenticated, explicitly configured LAN access.
2. Keep `assess_plan` on the task's pinned baseline when continuing its review.
3. Require current behavioral briefs for Fresh Eyes and repair resumable legacy runs.
4. Prune evidence by bytes before SQLite reaches capacity.
5. Contain recursive-input failures to the affected file, retaining other parse results.
6. Capture type-reference dependencies and conditional imports.
7. Keep local failures from triggering paid model fallbacks.

Patterns still include architecture scouts and CI/CD by default. The recommended review cadence is
documented rather than making everything opt-in; effective ordered-review overrides are explicit.
Dependency observations remain incomplete evidence, not proof that unseen consumers cannot exist.

Local checks passed: 636 core tests, 103 focused checks and 29 experiment-harness tests. Final CI
and deployment are separate qualification steps. Production source is 12,050 physical lines against
the unchanged 12,050-line budget after removing duplicated helpers and orchestration; no dependency
was added. The introductory README fell from 2,963 words/23,502 bytes to 599 words/4,574 bytes.
A context response on the same twelve-module Snow Rankings copy fell from 7,102 to 3,457 bytes,
without model calls. The [usage guide](usage.md) contains the operational routine and a copyable
AGENTS block.

## What the earlier run actually shows

The historical `snow-rankings-verified-2026-10-26` experiment used runtime `bc7790e`, not these fixes.
The quoted “51” means successful MCP calls during one milestone. The whole recorded run contains
110 MCP invocations: 103 succeeded and seven failed. It is not a count of model calls.

The ledger contains 34 completed/failed analysis outcomes, including three annotation timeouts;
unfinished calls may have no outcome. Two coding tasks hit 1,200-second limits, and analysis calls
had 600-second limits. Summed concurrent call durations are not workflow wall time. All recorded
dollar costs are unknown. Fresh Eyes saved one subsystem result but never completed product
reconciliation. Saved Pattern results occupy 73,719 and 79,434 bytes, the Fresh Eyes result 11,096
bytes, and the portable record 223,623 bytes; historical MCP wire bytes cannot be reconstructed.

Both treatment and control passed 29/29 acceptance checks. Agent-authored adoption notes exist,
but there are no structured adoption events or independently verified recommendation-to-patch
links; some reviews followed implementation. This does not establish architectural improvement
or a causal advantage.

For [usage feedback](usage.md#learning-from-real-use), ask: What was consulted? Which advice changed
the patch? What was rejected or missed? Was the next change easier, and what time/cost did using
the tool add? Require concrete examples. The [compact evidence](evidence/verified-rerun-audit.json)
keeps historical measurements separate from current local checks.
