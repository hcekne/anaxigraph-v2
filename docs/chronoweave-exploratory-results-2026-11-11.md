# ChronoWeave comparative results and development priorities

ChronoWeave completed all 189 checkpoints: three conditions, three repetitions each,
and 21 successive product requests per run. AnaxiGraph produced smaller change surfaces
and fewer import cycles, but needed far more safety repairs and substantially more time.
These results do not establish an advantage over a written architecture charter.

The conditions were a plain coding agent, the same agent with the charter, and the
charter plus AnaxiGraph. All used GPT-6.1 Sol with high reasoning effort. The comparison
below describes the completed exploratory continuation, including inherited work and
repairs. It is not the original preregistered experiment's winning verdict.

The [aggregate evidence](evidence/chronoweave-exploratory-2026-11-11.json) retains each
repetition rather than only the averages. Raw candidate repositories and transcripts
remain outside this repository.

| Measurement | Plain | Charter | AnaxiGraph |
| --- | ---: | ---: | ---: |
| Completed checkpoints | 63/63 | 63/63 | 63/63 |
| Final hidden checks, each run | 96/96 | 96/96 | 96/96 |
| CI checkpoints passing on the first attempt | 44/48 | 43/48 | 13/48 |
| Repair sessions | 4 | 6 | 46 |
| Existing files edited per late request, mean | 3.94 | 4.75 | 3.44 |
| Final production lines, mean | 4,102 | 4,652 | 3,984 |
| Final import cycles, mean | 1.00 | 0.67 | 0.33 |
| Final canonical test obligations, mean | 1,517 | 2,160 | 2,450 |
| Tests selected for an individual source file, mean | 916 | 1,284 | 1,062 |
| Selected fraction for an individual source file, mean | 60.3% | 59.1% | 43.4% |
| Final full reference suite with coverage, mean | 7.8 min | 6.5 min | 18.4 min |
| Coding plus analysis per run, recorded mean | ≥273 min | ≥276 min | 644 min |
| External verification per run, mean | 179 min | 273 min | 622 min |

These test-selection measurements are averages over individual source files, not the
union for a real multi-file patch or measured production CI time. Three historical
coding durations and two usage streams are missing in the comparison arms, so their
costs are known lower bounds. External verification is separate from coding and analysis;
parallel execution means these durations cannot be added to infer campaign wall time.

## What improved

AnaxiGraph's agents changed fewer existing files per late request, particularly compared
with the charter. Their final code also had fewer import cycles and slightly fewer
production lines. Those are useful signals for the goal of independently changeable
components. They are structural and edit-surface proxies, not proof that future work
will be easier or that AnaxiGraph caused every difference.

All final consumer checks reached recall 1.0 within observed coverage, and all final
sampled faults caught by the reference suite were caught by the selected tests. The
same external safety check and repair feedback applied to all three conditions. Final
qualification therefore cannot be credited solely to AnaxiGraph's advice.

The charter also did not consistently improve on plain use. It reached the same hidden
behavior, used more production lines and changed more existing files per late request.
Its reference suite was faster on average, but its final selection fractions ranged
from 36.4% to 81.8%, compared with plain's 54.0% to 64.8%. The outcomes show substantial
variation in design and test strategy even with the same model and task sequence.

## Why the safety and cost results were poor

1. **Tool access worked; formal plan suggestions were rarely adopted.** All 63 treatment
   coding sessions obtained substantive context, inspected the map and assessed a plan
   before their first visible edit, then scanned and validated afterward. The audit
   found 19 distinct checkpoint/proposal dispositions: 18 rejections and one adoption.
   These formal proposals came from a deterministic extension-family heuristic, not
   the model scouts. It incorrectly treated unrelated components as one family because
   they imported common error or validation helpers. A shared utility dependency is
   weak evidence of a shared responsibility. The adopted operation-discovery proposal
   corresponded to code, but the charter also encouraged that design; attribution is
   limited. These decision counts do not measure every informal use of map context.

   The models did identify specific opportunities, including limiting queued work to
   available worker capacity and distinguishing retryable delivery failures. The
   high-rated capacity recommendation was inspected during four later requests, but
   its affected module remained unchanged through the final snapshot. The formal
   assessment path consumes qualifying architecture-scout findings, not Pattern
   recommendations; those scout findings were optional low-priority splits. A useful
   finding therefore reached the agent without entering tracked adopt/defer/reject
   decisions. Whether it was intentionally deferred is unknown. This is a concrete
   gap between generating advice and recording what happens to it, not evidence of
   an observed production failure.

2. **Component-local selectors missed tests elsewhere.** A module's public contract did
   not stop API, shared-state and integration tests outside its own folder from executing
   its code. Coverage found those consumers after the agent submitted its change. The
   resulting omission reports caused repeated repairs. Only 13 of 48 treatment CI
   checkpoints qualified initially, versus 44 plain and 43 charter. These raw statuses
   include infrastructure incompleteness as well as actual safety gaps: for example,
   charter's third run had a serialization-only failure that did not represent an
   unsafe selector. Preserve both the first results and the repaired results.

   Initial selectors in all three plain runs conservatively fell back to all tests
   when consumer inventories or test/helper contents changed. The initial AnaxiGraph
   selectors checked declarations and target existence but did not invalidate a
   declaration when an existing test began consuming another component. Consumer
   obligations could therefore outgrow the manifest. Initial plan assessments explicitly
   limited their judgment to placement and lexical reuse, with behavioral correctness
   unresolved. There is no recorded evidence that the tool told an agent to omit a
   particular known consumer; it failed to supply equivalent freshness protection.
   The agents proposed replacing downstream tests with narrower contract checks, but
   the assessor did not establish that those replacements covered the same obligations.
   Saved CI reviews did warn to verify consumer cut points before narrowing. Those
   general warnings were useful but did not supply the missing execution evidence.

3. **A smaller fraction hid a larger test population.** AnaxiGraph selected about 1,062
   canonical tests per source file, more than plain's 916, despite its lower percentage.
   It selected fewer than charter's 1,284 on average, but its complete reference suite
   was much slower. The final raw test populations averaged 2,457, 2,169 and 1,540 for
   AnaxiGraph, charter and plain respectively. Fractions alone would misstate the CI
   benefit. Test cost and duplication matter alongside scope.

4. **Generated verification code repeated expensive work.** Recorded profiles in the
   final treatment run identified repeated pytest collection subprocesses in consumer
   union checks, selector parsing and path validation. Request 19 had three full
   coverage timeouts at 1,800 seconds before a later attempt completed. That attempt
   still omitted 25 consumer pairs; the next repair qualified. Request 20 initially
   omitted six pairs and needed another repair. These are measured mechanisms in that
   run. Per-test timings from the final external JUnit files were not retained, so they
   do not establish a complete timing breakdown across all nine runs.

5. **One treatment never received its scheduled architecture reviews.** In repetition
   two, seed mapping completed but subsequent mapping attempts were partial or failed
   at every request. All three scheduled Pattern workflows stopped in annotation
   prerequisites, before pattern screening, detailed review, architecture scouts or
   CI review. An identity filter mistook an ordinary public data label for an internal
   source name. That defect is fixed on main at `f4609aa`; it was absent from the frozen
   benchmark runtime. Structural queries and partial semantic information still worked.
   This repetition measures delivery failure as part of using the product, not a
   completed test of the intended full-review workflow. Fresh Eyes was not scheduled
   in this benchmark.

   The first two treatment repetitions did complete all three scheduled Pattern
   workflows. They still qualified only 11 of 32 CI checkpoints initially, versus
   charter's 30 of 32. Repairing workflow delivery is necessary but does not explain
   away the selector and advice problems in the completed reviews.

6. **The agent loop accumulated avoidable friction.** The treatment made 1,053 MCP
   calls, including 352 in repair sessions. There were 66 rejected requests: 44 stale
   checkout errors, nine proposal-ID errors, 12 argument-validation errors and one
   missing entity. No approval or unavailable-tool error was found. Text replies
   totalled 8.50 MB. Direct analysis and validation averaged 41 minutes per run; coding
   sessions, including repair and their own test waits, averaged 603 minutes. Faster
   mapping alone would therefore leave most of the measured time burden untouched.

The plausible overall explanation is that the charter supplied a useful architectural
starting point, while AnaxiGraph added a mixture of helpful context, noisy proposals,
failed review delivery and repeated evidence-repair interactions. The records support
those mechanisms. They do not isolate how much each caused the architectural outcome;
initial design choices, different generated tests and stochastic variation also matter.

## What should change next

- Treat dependency evidence as evidence of interaction, and require stronger responsibility
  evidence before proposing that components share an owner or implementation family.
- Make observed consumer obligations available when designing a selector. Preserve those
  obligations through edits and require demonstrated replacements before removing tests.
- Measure absolute selected obligations, selected execution time and consumer recall together.
  Do not optimize percentages or add large repetitive suites to obtain reassuring counts.
- Reuse collection and parsing within each verification operation, and measure their cost.
  Keep genuinely shared contracts and state in scope rather than narrowing checks to win a metric.
- Make failed mapping or review preparation explicit before a coding checkpoint proceeds.
  Retain partial work and failed attempts, retry only failed work, and record stronger fallbacks.
- Reduce stale-request loops and repetitive context, then measure whether concrete advice
  changes implementation rather than counting compliant tool calls.

The next comparative run is AetherLoom, one repetition per condition: three runs total.
It is a pilot of the fixed product, with the same tasks, model, effort and safety criteria
across conditions. Its results must remain separate from these historical results.

## Evidence limits

The continuation retained the original 104 accepted checkpoints, added a uniform repair
allowance, and changed infrastructure to recover from executor and evidence-storage
failures. The product runtime changed from `d99383c` to `c0f6522` for lossless CI evidence
encoding; later source-blind data-label fixes were not adopted mid-campaign. Recorded
metadata-access flags and original unknown costs remain in the evidence.

All nine runs executed the three inline probes successfully. Two inherited rows lack
their probe tags, so the original reader reports missing probes for plain repetition one
and AnaxiGraph repetition one. Saved XML independently shows all expected cases passed;
the raw rows and warnings were preserved. One earlier selected-fault outcome and three
control reference-fault outcomes remain unknown. Final samples do not erase those
uncertainties or guarantee complete behavioral coverage. The original parent records
were hash-verified unchanged, and no active mutation journal remained at completion.

A separate post-campaign diagnostic replayed the retained selected-fault case against
the accepted source in a disposable clone. The unmutated sanity check passed; the
original logical fault was caught by its intended assertion in 404.6 seconds. Source,
environment metadata and cleanup checks passed. This establishes that the accepted
source catches that case under the replay conditions; it does not explain or overwrite
the historical unknown outcome. Its time is excluded from comparative costs.
