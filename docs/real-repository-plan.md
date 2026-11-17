# Real-repository plan

Agreed with the maintainer on 15 November 2026. The aim is an AnaxiGraph that works on a large
product repository, measured there, and stays small.

## Why

Every benchmark so far (Switchboard, ChronoWeave, AetherLoom and the anaxi_evals tracks) uses one
small Python package at the repository root. On 15 November release 1.0.0 (`2afdf7a`) was run for
the first time on the intended kind of target: a private product repository of about 500,000 code
lines, with 1,401 Python files in several projects one folder below the root and 979
TypeScript/JavaScript files in one frontend. Nothing identifying that repository is recorded
here; only its shape and the measurements are.

## Measured baseline, 1.0.0 on that repository

| Measurement | Result |
| --- | --- |
| Imports between the repository's own Python files that the map links | 0 of about 11,280 |
| Cause | Module names are derived from the repository root or a `src/` folder; these projects import from their own folder (`app.…` inside `backend/`) |
| TypeScript/JavaScript | 979 files fingerprinted, none parsed; 82 of 95 recent fix/feature pull requests touch them |
| First scan | 46 to 53 s, 0.8 to 1.2 GB of memory |
| Rescan after a comment-only edit to one file | 19.5 s; after adding one Markdown file 17.9 s |
| Where a small rescan goes | Saving the whole map and reading it back, about 85%; the edited file itself, about 2 s |
| `prepare_change`, `assess_plan` | 3.5 to 6 s per call |
| Closed "families" found once imports link | 38; one has a contract file that declares a Protocol or ABC; three are attached to every brief |
| File suggestions for 30 replayed pull requests | A really changed file in the top three for 16; plain keyword search (BM25) 15 |
| The repository's own CI | Python tests 32 to 44 minutes on every Python change, with no selection |

A runtime patch that names modules from their import root (the first ancestor folder without
`__init__.py`, nearest root preferred) links 8,249 of those imports. Another 2,964 import a
module-level variable or a re-exported name, which the resolver does not accept as a target.

From the 13 November anaxi_evals pilot (nine chains): the declared-input CI planner answered
`not_configured` 147 of 147 times, `validate_change` answered `incomplete` 48 of 48 times, all 47
decision-required proposals came from the extension-family rule, and 32 of 512 tool calls were
refused with "Checkout changed after mapping".

## Steps

Each step lands with its measurements on the target repository and its measured size.

0. **A true baseline.** CI green; a map-health report in `scan`/`status` and a timing script;
   a test fixture shaped like the target; reviews requested rather than scheduled.
1. **Python imports link.** Import roots, and imports of variables and re-exported names.
   Done when at least 98% of internal imports link and 95% of test files link to code.
2. **Fast, and never refuses.** Per-file facts and storage, links built from those facts, the map
   kept in memory, automatic refresh on every call. Done when the next answer after a one-file
   edit takes under 2 s, a Markdown edit costs nothing, a new process or worktree is ready in 5 s,
   a brief takes under 1 s and no call is refused for a changed checkout.
3. **TypeScript and JavaScript.** A second reader for the same per-file facts: imports, exports
   and top-level declarations, using tree-sitter. No call graph, type checking or path aliases.
   Done when 98% of local imports link and these files appear in suggestions.
4. **Less noise.** A family only where members share a base class or protocol, and no heuristic
   forces a decision; reviews on request and scoped; fewer tools.
5. **Tests picked from a recorded run.** The project records which test executes which file;
   AnaxiGraph reads that record, selects the tests that ran a changed file plus changed tests, and
   selects everything for an input the record does not know. This replaces the declared-input
   planner. Done when, replaying 100 merged pull requests, no needed test is left out.
6. **Suggestions that beat keyword search.** A ranking signal ships only if it wins on the replay.
7. **A paid comparison on real pull requests.** Same model with and without the tool; the pull
   request's own tests decide. Not before steps 1 to 3 pass.

## Deliberately left out

Call graphs and type inference; parsers for further languages; links from frontend calls to
backend routes until the replay shows the need; tracking every finding through decision,
implementation and verification; new small benchmark applications.

## Status

| Step | State | Measured on the target repository |
| --- | --- | --- |
| 0 | Done | CI green again; ceiling 15,000 lines by maintainer decision; `map_health` in `scan` and `status`; `scripts/map_health.py`; fixture in `tests/test_real_layouts.py` |
| 1 | Done | 11,776 of 11,810 internal imports linked (99.7%); the 34 left name a module that two projects both provide. 640 of 700 test files link to code; the other 60 contain no static import of the repository's code (they read files, start subprocesses, import dynamically or test an installed package). Production size 13,733 lines |
| 2 | Done | Same graph, edge for edge. Rescan after a one-file edit 0.6 s (was 21 to 31 s); a brief 0.23 s (was 5 s); after a Markdown-only change 0.3 to 0.7 s (was 18.6 s; the target of no cost is not met, a new snapshot is still recorded); a new process on a mapped checkout 2.1 s (was 13.7 s), 3.9 s when it meets an edit nobody scanned; a new worktree 5.1 s; first read 15 s (was 59 s); peak memory 309 MB (was 1,049 MB); no call refused for a changed checkout (was 2 of 2). Production size 14,190 lines |
| 3 | Done | 979 TypeScript/JavaScript files read (2,380 modules in all); all 3,864 local script imports linked, none missed; 24 asset imports and 3,519 package imports left external; 502 of 507 frontend production files have a known consumer and 332 of the 465 frontend test files link to code (the other 133 are browser specs that import none); four files have syntax the pinned grammar does not accept and are reported. With both languages: first read 19 s (was 15 s), rescan after a one-file edit 0.6 s, brief 0.29 s, new process 2.3 s, new worktree 5.6 s, peak memory 384 MB. Production size 14,585 lines |
| 4 | Done | A family now needs classes that share a base class or protocol: the target has one closed family (28 model modules on one base class) where the old rule found 38, of which one had an interface. Its finding is advice under `architecture.advisory`; only reviewed proposals ask for a decision, and deferring needs no extra field. Agents are offered 15 tools instead of 20 (8,287 bytes of names, descriptions and schemas instead of 9,604); the other five stay in the CLI and dashboard. Reviews were unscheduled in step 0. Production size 14,584 lines |
| 5 | Implemented; qualification incomplete | `record-tests`, `tests`, `tests --ran`, the `tests_for_change` tool, and a reads recorder that runs in the project's own test process. From one recorded run of the target's backend suite (632 test files, 6,615 tests) and its 70 most recent fix/feature pull requests that touch that scope: no pull request left out a test file that executed or read a changed file or a changed test, and 2 select the whole suite where 31 did before the recorder. Ten faults planted one at a time, the whole suite run for each: nine made test files fail, and every newly failing one was inside the selection (18, 1, 61, 8, 46, 1, 1, 33 and 61 files; the fifth, a changed migration, selects the whole suite); the tenth failed no test. The limit: 86 test files start another process that could read the checkout, they are 39% of suite time and run for every change, so the median pull request runs 69% of suite time (mean 72%). The declared-input planner left the product for `experiments/declared_ci/`; the supplied-evidence audit and gate stay, see below. Production size 14,924 lines |
| 6 | Measured; nothing shipped | 77 pull requests replayed at their parent commit (13 Python only, 30 TypeScript/JavaScript only, 34 both; a median of six production files really changed). A really changed file is in the top three suggestions for 41, against 43 for plain keyword search (BM25); in the top one for 27 against 21. Reading TypeScript raised the product from 26. Adding files that changed together in history gave 48, with 13 pull requests better and 8 worse than keyword search (sign test p = 0.38), and 23 against 19 on the half it was not tuned on; boosting import neighbours gave 41. By the rule above no signal ships on this evidence. On TypeScript-only pull requests the product is behind keyword search, 14 against 18. Ranking a brief takes 0.17 s |
| 7 | Not started | Needs a budget decision |

## What is still open

- Tests that start another process. Recording inside the Python processes they start would
  let them be selected like any other test. Replaying the 70 pull requests as if that were
  done gives a median of 44% of suite time (25th percentile 11%, mean 51%), against 69% now.
  On the target the heaviest of them run the application's own git client, which starts a
  Python process on a script of the repository and git on temporary repositories.
- A recorded run with the git rule as it is now. The rule was widened after the run above
  (a repository created or copied elsewhere, a question about commit names, no command) and
  tightened (a path given as the value of an option), so the 39% is from before that.
- A module that is loaded by path is not attributed to the tests that loaded it. A changed
  migration therefore selects the whole suite; a new one selects the tests that scanned its
  folder.
- Shadow mode in the target's own CI: a nightly job that records, and `anaxigraph tests --ran`
  on pull requests. These are changes to that repository and its CI cost.
- The supplied-evidence audit and gate have moved to `experiments/declared_ci/`. The drift
  runner applies its own qualification without adding that protocol to ordinary product use.
- A release: these changes are on `main` only.
- Step 7.

## Evidence for step 5

On 15 November the target's backend suite was run once with per-test coverage of the
application code. For the 63 most recent fix/feature pull requests that touch backend code, the
tests that executed a changed file were a median 10% of tests and 17% of the suite's time.
Static imports cannot make this selection: the test files that import a changed file directly
are a median 60% of those that executed it, and following imports transitively reaches nearly
every test file. Selecting from executed lines alone left 31 of 70 pull requests with the whole
suite (18 for a JSON data file, 11 for a new migration) and, as a review of the rules against
the target showed, would have selected nothing for a changed migration, which coverage was
not measuring, or for a removed module that something still imports.

On 16 November the suite was run once more as a nightly job would run it: coverage of the
application and the migrations with one context per test, the reads recorder and timings, in
57 minutes on a loaded machine. The same 16 test files failed as in a run without the
recorder. The record holds 533 executed sources, 590 loaded ones (121 with a function that ran
outside any test or first loaded inside one), 2 known not to be loaded, and 2,875 compared
files in 0.56 MB. Tests read 621 files as data, 53 of them outside the suite's folders
(workflow files, shell scripts, infrastructure and frontend files that contract tests check),
scanned 39 folders, and read 13 files while a module was imported.

| Replay of the 70 pull requests | Whole suite | Median share of suite time | Mean | Under 20% |
| --- | --- | --- | --- | --- |
| Rules of 15 November, application coverage only | 31 | 65% | 57% | not counted |
| This run without its reads | 31 | 66% | 58% | 25 |
| This run with its reads | 2 | 69% | 72% | 0 |
| The same, were process-starting tests attributable | 2 | 44% | 51% | 20 |

No row leaves out a test file that executed or read a changed file or a changed test. With
reads, the two whole-suite selections are a dependency list and a test file that the runner had
read to print a warning; the second no longer counts as an input and was not measured again.
19 further pull requests reach nearly the whole suite by rule: they are large and change what
a widely used module does at import. The floor of 40% is the 86 test files that start a
process (39.0%) and 23 with no recorded input (1.8%).

| Planted fault | Selected | Share of suite time | Newly failing test files | Left out |
| --- | --- | --- | --- | --- |
| A function body returns the opposite | 158 of 632 | 50% | 18 | 0 |
| A function that runs when its module is imported skips a registration | 208 | 52% | 1 | 0 |
| A value in a JSON file that the application reads once and keeps | 166 | 50% | 61 | 0 |
| A new migration that cannot be applied, in a folder that is scanned for them | 158 | 64% | 8 | 0 |
| An existing migration raises when it is applied | 632 | 100% | 46 | 0 |
| A value in a JSON file that a module reads when it is imported | 277 | 59% | 1 | 0 |
| An entry of a table that is built when its module is imported | 247 | 63% | 1 | 0 |
| A function that 150 test files execute returns the opposite | 215 | 55% | 33 | 0 |
| A function whose result is kept ignores a value it read | 196 | 52% | 61 | 0 |
| The default of a class attribute, in a module no test executed | 270 | 82% | 0 | 0 |

Each fault was planted alone and the whole suite run. Without the reads the third selects the
whole suite. The rules of 15 November also caught the first two. The changed migration selects
the whole suite because a migration is loaded by path and nothing imports it; all 46 newly
failing test files are among the 56 that executed it when recorded. The last fault failed no
test, so its run says nothing about what a selection leaves out; it shows a change that no
test checks still costing 82% of suite time: a class body runs when its module is imported,
and 186 test files test the modules that use that one.

## Follow-up qualification — 17 November 2026

Step 5 is implemented; qualification remains incomplete. The 70-change replay applies historical
paths to one checkout and one recording, not 70 independently executed PR transitions. Time
shares sum recorded JUnit durations, not measured CI wall time; 44% remains a counterfactual.

Implemented since the handover:

- The supplied-evidence audit and gate live in `experiments/declared_ci/`; the feature-drift
  runner applies its own qualification. Ordinary validation retains failed/stale/unchecked
  check states without requiring the experiment's selector protocol.
- Opaque starts outside a test retain a whole-suite fallback. Measured process-starting helpers
  keep every observed consumer selected; unmeasured helpers require the full suite.
- Failed testcase identities prevent an existing failure from hiding a second regression in
  the same file. Legacy baselines without those identities subtract no failures.
- `record-tests --start` pins source inputs. The recorder checks them at startup/completion,
  fingerprints coverage/JUnit output, declares xdist workers and counts finished tests. Invalid,
  mixed, missing or interrupted evidence cannot replace the last record. Unbound legacy records
  require full checks after a change. One pytest invocation, including xdist, is supported;
  separately launched/multi-machine shards need a completion protocol first.
- Shadow reports distinguish source-bound candidate invocations, unverified JUnit and missing
  recorded test files. Unmatched testcases are rejected. The supplied invocation remains the
  project's declaration, not independent proof of complete behavioral coverage.
- Module inspection and the dashboard show paged file-level runtime test consumers, baseline
  provenance, source/test changes and recorded duration share. This evidence is distinct from
  static links and permission to narrow CI. Advice now requires observed consumers rather than
  assuming folder boundaries imply isolated checks.

Direct Python-child attribution is opt-in. A standard-library bootstrap records reads and
source loads into one bounded log. Missing, late or malformed receipts and unobserved
processes retain conservative selection. Cached results retain consumers across module
boundaries. Shared ancestor dependency files and GitHub workflow/action changes widen checks;
an unrelated project's dependency list stays local.

A mutation probe found startup hooks reading data before the recorder loaded: three test files
failed, but only one was selected. Both parent and child cases now retain every failing test.
Unobserved executable `.pth` files or startup customizations keep full/opaque checks. The
exactly reviewed coverage initializer is compatible only while inactive. Observation format v2
prevents older or mixed observation rules from silently authorizing narrow selection.

The baseline recorded at `a7fa7f0` passed 892 product and 418 experiment tests on both
Python versions. Its source-bound record is 154,179 bytes; cached opaque-process consumers
still account for 25 test files and 77.8% of summed recorded durations, not CI wall time.
Current focused startup/recorder checks pass 170 tests. CI also exercises the installed wheel.
The result projector now uses the delivered response contract, removing duplicated field lists
and rejecting invented contracts for documentation-only markers. Production is 14,960 lines,
within the unchanged 15,000-line ceiling. No paid model calls or new target-suite runs were made.

Screening, Pattern detail and architecture now receive verified implementation for the selected
modules. Previously their isolated workers had facts and summaries without file access. Source
copies are transient; saved review requests retain fingerprints. Fresh Eyes remains behavior-only.
In a controlled pair with identical behavior briefs, Sol/medium detail rated unbounded fan-out 3
and an already bounded implementation 0; facts-only detail returned unknown for both. Screening
retained the real opportunity. Luna-only detail had produced a false positive on the bounded case.
The reproducible paid probe is documented in `experiments/architecture_quality/README.md`;
this demonstrates implementation sensitivity, not general architectural superiority. Production
was 14,987 lines, within the unchanged ceiling. No target-suite rerun or broad paid campaign was made.

Following the same real model result into task guidance exposed a delivery gap: its high-priority
Pattern finding appeared in preparation, but plan assessment reported no material proposals.
Patterns now use the existing reasoned disposition and validation-followthrough path. Both views
share stable review/module/card IDs and priority ordering. A replay of that saved result now
requires a decision and retains it through validation, while implementation remains unverified.
No additional model call was needed. Shared projection/filtering and removal of duplicate review
fetching keep production at 15,000 lines, without raising the ceiling.

Next: qualify sharded recordings and worker startup; refresh the target's whole recorder folder
before new measurements. Use prospective shadow runs and actual selected runs before claiming
CI savings. Better architectural advice needs real-task evaluation separately.

An audit then reproduced a plan with thirteen material findings that could never finish: only
twelve were shown and submitting them left the scope incomplete. Decisions now live in one
current task checkpoint, independently of optional trial logging, with resumable batches and
full inspection. Changed finding content requires a new disposition; source edits retain prior
obligations. Concurrent stale writes and a late assessment for another task are rejected.
Schema 9 backs up the existing store before upgrading and excludes local task plans from exports.

Agent-response profiling used the same frozen public `5c8933d` checkout for both runtimes:
309 modules, 20,882 nodes and 48,151 edges, structural only, no test record, trial logging off
and no model calls. Nine warm samples per operation on the shared development machine gave:

| Operation | Previous median | Updated median | Previous / updated reply bytes |
| --- | ---: | ---: | ---: |
| Inspect contracts module | 212 ms | 101 ms | 6,416 / 6,416 |
| Inspect application module | 228 ms | 105 ms | 6,136 / 6,136 |
| Validate unchanged checkout | 435 ms | 165 ms | 43,768 / 3,413 |

All 66 complete structural findings, all module facts, every page of both inspections and
extension test reach had matching canonical digests. Preparation and assessment had no material
improvement. Compact inspection now derives relationships once; comparisons reuse both graph
views. Validation counts unchanged observations and expands them explicitly. Regression fixtures
check that sixty old warnings cannot hide a new cycle/private access, equal counts cannot hide
changed evidence, and more than fifty new findings remain retrievable. These local timings do
not establish production latency, better architectural judgment or faster CI.

Source-role qualification used frozen public `c2dcd23` source (313 modules, 20,991 nodes).
All 45,258 ordinary reference edges and every prior test link matched the old indexer. Sixty-five
files with test functions and `conftest` imports gained a test role and 1,767 static test links;
default model scope fell from 194 to 129 modules. No model calls or elapsed-model savings were
measured. Explicit test-symbol lookup and manual review scopes remain available. The same frozen
nineteen eligible owner-replay cases retained 2 first-owner and 5 top-three hits: broader ownership
relevance remains unresolved. [Qualification record](evidence/test-role-qualification.json).

A recorded mutation fixture exposed two safety issues before shipping: root-level inputs were
outside an old `--scope .` prefix, and treating an inferred test role as a filename-convention
helper could omit a dynamic consumer. Root scopes now normalize correctly, ambiguous old scopes
require full checks after edits, and source-role hints retain measured runtime consumers. The
mutation fails both direct and dynamic tests, and selection retains both. Unknown native/shard
observations keep their existing fallbacks; this does not qualify broader CI savings.

A synthetic 133-finding qualification, recreating the application between batches, recorded all
133 decisions in one checkpoint with no model calls. Median assessment was 43.58 ms, the largest
reply 13,454 bytes and the fixture database 167,936 bytes. These measure small-fixture bookkeeping,
not architectural quality or a large-repository latency guarantee. HTTP, MCP and dashboard-handler
tests exercise the same batch progression; optional logging disabled/pruned, changed recommendations,
and concurrent task/disposition races have regression coverage.

The duplicate static "implemented" predicate and automatic folder-only CI prescription were also
removed. Import reach no longer predicts future test savings. Observed boundary deltas remain
available, while freeform proposal implementation stays unverified. Production is 14,999 lines
under the unchanged 15,000-line ceiling. No paid campaign or target-suite rerun was made.

Worker qualification exposed a separate safety defect in warm bytecode loading. A dynamic child
loaded a dotted source filename without retaining its module object. The recorder inferred the
wrong source from its cache name and marked the real input unloaded. A mutation failed two test
files, but selection omitted one. Direct execution-filename observations now retain both failing
files; the unrelated test stays out. Regression cases cover plain, dotted, optimized and externally
cached modules, including a nearby unused file that must not become a spurious input.

Observation format v3 preserves those execution inputs. V1/v2 reports remain readable but cannot
authorize narrow checks after a change. This repairs an observed omission; it does not establish
complete test recall. Separately launched shards and the conservative xdist-controller fallback
remain unchanged. Production is 15,000 lines under the existing ceiling. No target suite or paid
model run was started for this qualification.

A parallel-worker prototype captured Python startup and deferred final worker reports through
process exit. Tracing showed that both workers also run a native `uname -p` platform probe. That
process's reads remain unobserved, so full checks still apply. The prototype was retained outside
the repository rather than shipped as a narrow-selection improvement. No command-name exemption
was introduced. Separately launched shards still need a completion protocol.

The same audit found the dashboard's new-baseline button pinning a cached status snapshot after
source edits. It now asks the shared preparation operation to capture the working files directly,
in one request. Explicit restart clears prior plan intent and starts a new trial identity; ordinary
preparation stays pinned. The captured source identity survives an interleaved scan by another
process. CLI, MCP, real HTTP responses and dashboard handlers have regression coverage. Shared
snapshot-retention normalization removes duplication and keeps production at 15,000 lines.

Source-role reclassification exposed a misleading comparison: twenty unchanged private-access
observations in the frozen public checkout were labelled improved when their owners became tests.
Validation now distinguishes production/test scope changes, including indirect cycle changes,
from observed boundary changes. Newly relevant concerns remain visible, real new concerns lead
the compact list, and full validation expands boundary findings and rescoped paths. The same
313-module source retained identical annotation fingerprints and CI review context after sharing
the canonical relationship view. These are compatibility and classification checks with no model
calls, not evidence of better design. Production remains at the unchanged 15,000-line ceiling.

Query profiling then exposed a validation race: a source edit between review and test selection
could qualify old checks while selecting against different files. Validation now keeps one captured
snapshot, verifies its input identity before qualification, and reports `checkout_current=false`
with completion unqualified if files moved. It keeps the original baseline and attributes supplied
checks to their actual candidate; the next call refreshes automatically. Real recorded-consumer
and all-transport regressions reproduce the old false qualification.

Identical baselines now reuse the captured graph, internal findings copy only delivered evidence,
and module inspection stops computing discarded relationships. In three shared-machine samples on
the same 2,380-module structural source, unchanged-validation median was 726.92 ms before and
159.15 ms after; inspection was 393.30/300.44 ms. Existing reply fields were identical, with one new
freshness field. The separate 13-module case retained eleven current, one stale and one missing
annotation. These are API qualification timings, not changed-candidate, model or architectural
quality claims. Two unused internal wrappers were removed; production is 14,999 lines under the
unchanged ceiling. [Qualification](evidence/query-validation-qualification.json).

Saved reviews then exposed a delivery gap: an unrelated edit hid otherwise compatible scoped
advice, while explicit cache reuse changed proposal identities and duplicated decision obligations.
Availability now checks scoped semantic inputs, resources and result validity while preserving the
original capture. Compatible reuse keeps original proposal identities and decisions. A stale old
request cannot promote a result after its own source changes. Product Fresh Eyes reconstruction
uses durable child results and checks their digests; transient request pruning no longer invalidates
it. Architecture preparation keeps its three-option reference alongside material proposals.

In a controlled 104-module fixture seeded by the deployed runtime, the old implementation hid all
advice after an unrelated edit and added two pending duplicate obligations on reuse. The update
retained all advice and both existing decisions. Availability reads left captures untouched. The
five-sample preparation median was 12.46 ms for the old empty reply and 16.52 ms with advice
delivered. These are delivery/identity checks using synthetic saved results, not new model or
architectural-quality evidence. Twenty-seven regression cases cover compatibility, invalidation,
decision continuity and product inputs. Production remains 15,000 lines under the unchanged ceiling.
[Qualification](evidence/review-delivery-qualification.json).
