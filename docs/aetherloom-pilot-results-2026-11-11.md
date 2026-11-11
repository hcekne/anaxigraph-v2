# AetherLoom: one-run comparison of plain, charter and AnaxiGraph

This pilot does not demonstrate that AnaxiGraph produces better architecture or safer,
faster CI than a written charter. Its model workflows worked, and the coding agent read
relevant advice, but that did not produce complete consumer-aware test selection.
Exact-target test expectations also complicated the chosen repair strategies.
Both issues matter when interpreting the result.

All three runs exhausted their two permitted repairs before the final request. Of 63
planned checkpoints, 35 qualified, three were attempted but unqualified, and 25 were
never attempted. Request numbering starts at zero.

| Outcome | Plain | Charter | AnaxiGraph |
| --- | ---: | ---: | ---: |
| Qualified checkpoints | 15/21 | 14/21 | 6/21 |
| Last qualified request | 14 | 13 | 5 |
| Stopped at request | 15 | 14 | 6 |
| Hidden checks at the stopped request | 82/82 | 78/78 | 38/38 |
| Terminal qualification problem | Two selector-expectation tests fail after widening | 24 omitted consumer pairs | 1,085 omitted consumer pairs |

The hidden-check totals cover different feature stages and cannot rank final quality.
Plain's final suite had 3,440 passes and two failures; charter and AnaxiGraph passed
their own suites but retained consumer gaps. No run reached the third probe or the
requested final architecture. The campaign ran for 6 hours 13 minutes. The
[aggregate evidence](evidence/aetherloom-pilot-2026-11-11.json) includes matched comparisons,
terminal attempts and all incurred recorded costs; raw attempts remain preserved privately.

Each condition received the same 21-request AetherLoom specification, coding model
(GPT-6.1 Sol, high effort), private-workspace restrictions, hidden behavior checks and
two-repair allowance. There was one evolving application per condition: three runs,
not nine. All incurred attempts count; later requests after a terminal failure remain
unattempted. One replicate cannot establish a causal or statistical winner.

The runtime was frozen at `288713e2c491096bebc311803709f4dd15902d78`, with
[passing source CI](https://github.com/hcekne/anaxigraph-v2/actions/runs/42960797693).
Its product source
contains the mapping fix missing from the historical ChronoWeave runtime. Native
isolation and real paid MCP access were checked before coding. Mapping used product
defaults, starting with Luna/low and stronger fallbacks; the coding model did not
replace those analysis profiles. Patterns, architecture scouts and CI review were
scheduled at requests 6, 11 and 16. Only request 6 was reached in the treatment;
11 and 16 remained unattempted. Fresh Eyes was not scheduled.

## Compare the same work

All three conditions qualified through request 5, including 32/32 hidden checks.
Their cumulative costs through that common point, including repairs, were:

| Measurement through request 5 | Plain | Charter | AnaxiGraph |
| --- | ---: | ---: | ---: |
| Coding time | 34.8 min | 53.7 min | 53.0 min |
| Analysis and validation, separately | 0 | 0 | 4.6 min |
| External verification, separately | 2.6 min | 5.4 min | 4.5 min |
| Recorded coding input tokens, including cached context | 3.51 M | 4.84 M | 7.30 M |
| Reference test population | 580 | 652 | 714 |
| Individual source files measured | 14 | 23 | 21 |
| Mean tests selected per individual source | 499 | 476 | 478 |
| Full reference suite with coverage | 35.7 s | 27.7 s | 29.0 s |
| Production lines | 1,026 | 1,151 | 1,113 |
| Largest production module | 270 lines | 280 lines | 310 lines |
| Functions over complexity 10 | 5 | 5 | 4 |
| Import cycles | 0 | 0 | 0 |

These structural proxies show mixed trade-offs. AnaxiGraph had fewer high-complexity
functions, but a larger maximum module and more code than plain. They do not show a
clear architecture advantage. Comparing its request-6 application with a control at
request 20 would confound tooling with different functionality and development stages.
Token counts include repeated cached context and exclude incompletely reported model-worker
input; they are not unique text volume or complete billing figures.
Selection averages also use different test populations and source granularity. Reference
coverage timing measures benchmark verification, not ordinary application CI duration.

## What failed, and why

**The selector knew declared components, not every executing consumer.** At request 6,
AnaxiGraph initially omitted 320 source/test-ID pairs in hypothetical individual-source
changes. Repair one reduced that to 182. Repair two created replay aliases for existing
test scenarios and reached 1,085 omitted pairs, with observed recall approximately
0.835. All 182 earlier gaps remained; 903 additional pairs came from new test IDs.
That is not 1,085 independently demonstrated bugs. The evaluator did not verify that
the new contract aliases replaced the original test obligations.

All three AnaxiGraph request-6 attempts passed their own suites and 38 hidden checks.
Their actual multi-file selectors included the full suite, whose reference runs passed.
Separately, all 30 reference-caught sampled faults were caught by the selected checks
for their mutated source files. The failure is
in future individual-source selection, which a broad current patch can hide. It still
matters: the next smaller change might omit an executing consumer.

**Useful advice arrived without reliable implementation follow-through.** The first
scheduled Pattern workflow finished all 52 batches in approximately 120 seconds,
using 60 model calls. Cheap screening reduced 2,394 module/card pairs to 54 detailed
evaluations. Five schema-validation failures recovered through stronger fallbacks.
The three architecture lenses produced 40 keep assessments, 14 unknown assessments
and no findings; the CI review returned one recommendation with partial input evidence.

Before its first request-6 source edit, the coding agent inspected the completed Pattern
and CI results and assessed a plan. The CI recommendation identified downstream API
consumer suites; 136 of the 320 initial omitted pairs were in those previously named
suites. The selector and its metadata remained unchanged in that first submission.
Other omissions involved tests introduced afterward, so the earlier review could not
have enumerated them. This supports a concrete gap between advice and fresh executable
consumer evidence, rather than a failure to make the tools available. Tests consistent
with a recommendation are not isolated proof of adoption when the task also required
them.

All seven main coding sessions followed the required context, inspection and plan
sequence. Yet every successful plan assessment reported no current material proposals:
no proposal IDs or adopt/defer/reject decisions were exposed. Compliant tool calls did
not require a concrete architectural change. Pattern opportunities and a CI warning
were not converted into a material plan decision with an executable completion check.

**Brittle tests and repair strategy consumed the allowance.** Charter later retained
24 omitted source/test-ID pairs involving 22 distinct tests through all three attempts
at request 14, with recall approximately 0.9979. Its focused contract replays did not
establish verified equivalence either. The final repair made no source changes: its
agent said widening conflicted with protected passing tests that pinned exact narrow
target lists. The harness did permit a staged correction: widen selection, allow the
old assertions to fail, then update those failing expectations in the next repair
while retaining test IDs. AnaxiGraph used that permitted sequence at request 5.
Charter spent its first repair on aliases instead, leaving the staged alternative
untried. This is an interaction between generated test design, repair strategy and
the bounded allowance; it is not evidence that the benchmark prohibited a correct
repair. The initial omissions remain real. No condition received an extra retry.

Charter's final request-14 application passed 1,766 own tests and 78 hidden checks,
including its second probe. Its actual whole-diff selection passed as well. Historical
unknown fault outcomes remain unknown; a later, different sample cannot erase them.

**Plain exposed the same freshness problem later.** Its request-15 tests added consumers
inside existing test files; its inventory check tracked filenames, not changed functions
and executing dependencies. The first submission omitted 145 pairs across 73 new tests.
Repair one included the missing sector-test family and reduced this to one policy API
consumer, which it proposed replacing without formal verification. It also added 991
test IDs: average selected tests increased from 1,497 to 1,733 while the percentage fell
from 62.5% to 51.2%. The lower percentage did not mean fewer selected tests.

Its last repair retained the original API consumer and then failed two old exact-target
assertions. It began the permitted staged correction with no further repair remaining.
Native verification confirmed those two failures, so its apparently complete observed
recall is unqualified and no final fault sample ran. All 82 hidden checks still passed.
Test-expectation friction and the chosen repair sequence affected both comparison arms;
they do not remove AnaxiGraph's earlier consumer gaps.

Plain's first request-15 reference suite took 767.3 seconds across 2,397 cases. The
slowest 20 cases accounted for only 4.1% of recorded testcase time: the cost was spread
across the suite. Source inspection found repeated selector parsing; repair one reduced
that work through per-call reuse. Its suite then took 594.3 seconds, but its population
and target sets also changed, so this is not a controlled speedup measurement. Native
reference coverage and fault testing are benchmark measurement costs, separate from
the generated application's ordinary CI duration.

## What this means for AnaxiGraph

1. Make selector freshness mechanical: changed tests, helpers, entry points and shared
   contracts must invalidate outdated consumer evidence. Use a conservative fallback
   when that evidence is missing, then recover smaller scope through verified contracts.
2. Convert material recommendations into explicit actions and verification. Recording
   that an agent read advice or assessed a plan does not establish that the relevant
   code, metadata and checks changed.
3. Measure test architecture as well as production architecture. Component folders do
   not create independent verification if tests still execute other components. Replaying
   large existing suites under new names adds cost without proving an independent boundary.
4. Improve the benchmark before drawing a stronger conclusion: make safe updates to
   selector-specific expectations possible without consuming a verification round merely
   to expose an expected assertion failure, while protecting behavioral assertions and
   test IDs. Provide an explicit way to prove replacement obligations. Retain original
   failures rather than rescoring this pilot under changed rules.

The earlier [ChronoWeave review](chronoweave-exploratory-results-2026-11-11.md) found
smaller change surfaces and fewer cycles alongside many more safety repairs and higher
cost. AetherLoom still exposes an advice-to-verification gap when workflow delivery works.
The tracks and repair allowances differ, so their outcomes are not a before/after
measurement of the mapping fix.
The priority is an executable connection from observed consumers and architectural
advice to implementation and proof, followed by another separately specified trial.

## Record integrity and interpretation

The terminal audits found no baseline or protected-source integrity discrepancies,
unclosed coding sessions, mutation journals or index locks. All 55 coding streams
were closed and retained unchanged. Review covered 1,046 commands and all 31 flagged
access events, with no observed reads of protected harness or other-arm content.
This static review does not prove the absence of every unrecorded access.

AnaxiGraph made 90 MCP calls, with five recovered stale-checkout errors and no approval
or unavailable-tool failures. Both comparison conditions made no MCP calls. The frozen
runtime, scenario and protocol fingerprints matched the recorded launch. Prelaunch
model and MCP qualification calls are retained separately from condition costs.

All recorded coding durations, coding usage and verification segments reconcile;
model-worker input and complete billed spend are not available. The analysis ledger
contains 95 calls and 71,489 output tokens. Two historical charter reference-fault
outcomes remain unknown. Later samples do not resolve those original cases. Fault
stages skipped after a failed baseline are unattempted, not successful zero-fault checks.
Across retained samples, 345 reference-detected faults were also caught by selected tests;
13 samples were not caught by the reference suite and two had unknown reference outcomes.
These samples differ across implementations and do not establish complete fault coverage.

The aggregate file keeps qualified and raw observations separate, including plain's
unqualified final recall, each condition's attained horizon, terminal attempts and
all incurred costs. Original attempts were not overwritten or rescored under different
repair rules. These results support targeted fixes and another evaluation; they do
not establish a completed final-architecture comparison.
