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
| 5 | Done, with one part left in place | `record-tests`, `tests` and the `tests_for_change` tool. On the target's backend suite (632 test files, one recorded run) and its 70 most recent fix/feature pull requests that touch that scope: no pull request left out a test file that executed a changed file or a changed test; 39 are selected and run a median 10% of suite time (75th percentile 30%); 31 select the whole suite, 18 because a JSON data file changed, 11 because a migration was added that the recording did not measure, 2 for another unattributable input. Over all 70 the median is 65% and the mean 57% of suite time. A rule without the import-time and unattributable-input broadening would pick a median 13% and could miss tests. The declared-input planner left the product for `experiments/declared_ci/`: no tool, command, route or dashboard form offers it, and the drift qualification passes against its new home. The supplied-evidence audit (`analysis/ci_obligations.py`) and the evidence gate in `validate_change` stay because the feature-drift runner requires them from the installed runtime; they are inert unless a checkout carries `ci.json` or a policy file. Production size 14,309 lines |
| 6 | Measured; nothing shipped | 77 pull requests replayed at their parent commit (13 Python only, 30 TypeScript/JavaScript only, 34 both; a median of six production files really changed). A really changed file is in the top three suggestions for 41, against 43 for plain keyword search (BM25); in the top one for 27 against 21. Reading TypeScript raised the product from 26. Adding files that changed together in history gave 48, with 13 pull requests better and 8 worse than keyword search (sign test p = 0.38), and 23 against 19 on the half it was not tuned on; boosting import neighbours gave 41. By the rule above no signal ships on this evidence. On TypeScript-only pull requests the product is behind keyword search, 14 against 18. Ranking a brief takes 0.17 s |
| 7 | Not started | Needs a budget decision |

## What is still open

- A mutation probe of test selection on the target: do the selected tests catch a planted fault
  that the whole suite catches?
- Attribution for data files and dynamically loaded modules, which select the whole suite today
  (29 of the 31 whole-suite selections). It needs either a recording that also measures those
  modules, or a recorded list of which module read which file.
- The supplied-evidence audit and gate (`analysis/ci_obligations.py`, about 300 lines, and the
  gate in `application/delivery.py`) serve only the feature-drift runner and should move to it.
- Wiring into the target repository: an MCP entry, an ignore rule for `.anaxigraph-v2/`, and a
  nightly job that records the suite. These are changes to that repository and its CI cost.
- A release: these changes are on `main` only.

## Evidence for step 5

On 15 November the target's backend suite (6,615 tests) was run once with per-test coverage.
For the 63 most recent fix/feature pull requests that touch backend code, the tests that executed
a changed file were a median 10% of tests, 15% of test files and 17% of the suite's time; 34 of
the 63 needed under 20% of the time and 6 needed over 80%. One backend file is executed by a
median 0.4% of tests (90th percentile 4.9%). Static imports cannot make this selection: the test
files that import a changed file directly are a median 60% of those that executed it, and
following imports transitively finds them but reaches nearly every test file. 57 backend files
ran only at import time, so a rule based on executed lines alone would miss changes to
module-level values; step 5 has to broaden for those.

Still unmeasured for step 5: whether the selected tests catch a fault planted in changed code
that the whole suite catches. The rules are conservative by construction for executed lines and
import-time code; a mutation probe on the target has not been run.
