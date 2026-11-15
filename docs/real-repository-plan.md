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

| Step | State | Measured |
| --- | --- | --- |
| 0 | In progress | CI repaired; ceiling set to 15,000 lines by the maintainer (13,549 used) |
| 1 to 7 | Not started | |
