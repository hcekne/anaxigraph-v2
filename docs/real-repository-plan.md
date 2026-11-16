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
| 5 | Done; one limit measured | `record-tests`, `tests`, `tests --ran`, the `tests_for_change` tool, and a reads recorder that runs in the project's own test process. From one recorded run of the target's backend suite (632 test files, 6,615 tests) and its 70 most recent fix/feature pull requests that touch that scope: no pull request left out a test file that executed or read a changed file or a changed test, and 2 select the whole suite where 31 did before the recorder. Three faults planted one at a time, the whole suite run for each: every newly failing test file was inside the selection (18, 1 and 61 files). The limit: 86 test files start another process that could read the checkout, they are 39% of suite time and run for every change, so the median pull request runs 69% of suite time (mean 72%). The declared-input planner left the product for `experiments/declared_ci/`; the supplied-evidence audit and gate stay, see below. Production size 14,924 lines |
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
- Seven more planted faults whose whole-suite runs were still going when this was written: a
  new and a changed migration, data read while a module is imported, a module-level value, a
  class attribute in a module no test executed, a cached function and a widely used function.
  `anaxigraph tests --ran` on their JUnit reports gives the result.
- A module that is loaded by path is not attributed to the tests that loaded it. A changed
  migration therefore selects the whole suite; a new one selects the tests that scanned its
  folder.
- Shadow mode in the target's own CI: a nightly job that records, and `anaxigraph tests --ran`
  on pull requests. These are changes to that repository and its CI cost.
- The supplied-evidence audit and gate (`analysis/ci_obligations.py`, about 300 lines, and the
  gate in `application/delivery.py`) serve only the feature-drift runner and should move to
  it. 76 lines are left under the ceiling.
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

Each fault was planted alone and the whole suite run. Without the reads the third selects the
whole suite. The rules of 15 November also caught the first two.
