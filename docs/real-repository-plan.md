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

Local evidence: 848 product tests passed in the end-to-end instrumented run, producing a 128,758
byte record. The subsequent focused regression run passed 80 tests, including module-inspection
paging and stale-source/test attribution. CI is green on `21f8fc9`; its existing suite publishes
source-bound test records on Python 3.11 and 3.12. The first instrumented CI attempt exposed
nested coverage output collisions; invocation-specific configuration and isolated fixture outputs
fixed them. Production size is now 14,803 lines, 121 fewer than the handover; the 15,000-line
ceiling is unchanged. No paid model calls or new private-target measurements were made here.

Next: attribute Python child work without dropping unobserved descendants; qualify sharded
recordings; refresh the target's recorder before new measurements. Use prospective shadow runs
and actual selected runs before claiming CI savings. Better architectural advice needs real-task
evaluation separately. Reuse scheduled verification where possible rather than duplicating suites.
