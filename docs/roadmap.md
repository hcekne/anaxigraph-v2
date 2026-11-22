# Roadmap: one number

The direction was set by the maintainer on 18 November 2026, who agreed to the phases below the
same day. The decisions listed near the end stay the maintainer's as each one comes up. This continues the
[real-repository plan](real-repository-plan.md): its eight steps made AnaxiGraph work on a large
repository, and this says what that is for.

## The goal

In the maintainer's words: "a sidecar coding buddy that will help agents and humans produce
better code, with better architecture, better CI/CD and more efficient modular development".

## What to do next — 22 November 2026

1. **Check one real prediction.** The nightly backend test recording succeeded on 22 November.
   There are **0 of 30** saved pull-request comparisons. Both jobs still use `d7cbdb2`;
   the 42.8% median on 70 historical changes was measured with the later workflow rule in
   `bdb0ed4`. The pull-request job also supplies JUnit alone. In a local two-file test suite,
   AnaxiGraph selected the one test file with a new failure. JUnit alone labelled the result
   `unverified`; adding the candidate reads report tied the same result to the tested checkout.
   With the trial maintainer's agreement, use the same newer revision in both jobs and record
   the full pull-request test run. Inspect the first
   saved result for the share selected, its evidence status and any failure outside the pick.
   Count each pull request once. [Runnable comparison](usage.md#compare-a-prediction-with-a-full-pull-request-test-run)
2. **Find the code a developer should edit.** On a fresh map of V2 at `6f94f10`, we asked,
   “Before editing a file, show how much of the recorded test suite would run if I change a
   single function or change a top-level declaration.” All three suggestions were experiment
   files. One relevant owner is `src/anaxigraph/application/selection.py`, where `test_reach`
   supplies those figures to preparation and plan assessment. The dashboard showed the brief as
   roughly 6,200 characters of JSON in the original walkthrough.

   We tried keeping the first owner and filling the other places with keyword search. It put
   that owner second on the example. On 77 saved changes, the top three included a file actually
   edited on 47 changes, versus 41 for the current tool and 43 for keyword search alone.
   On the existing 38-change evaluation split the counts were 20, 19 and 19. Leave the rule
   in the experiment. Try it on fresh changes, and check whether a developer can identify the
   owner, its public entry point and a relevant test from the brief. That gives us a concrete
   task for assessing the dashboard too. [Replay procedure](../experiments/real_repository/README.md)
3. **Measure whether the help pays for itself.** The earlier ChronoWeave trial found fewer
   files touched per late change, but 46 repair sessions with AnaxiGraph versus four with a plain
   agent. Phase 7 compares the same model on the same real tasks, scoring correctness,
   how much testing a change needs, boundary violations, time and cost. The maintainer's
   spending limit is still open. [Earlier trial](chronoweave-exploratory-results-2026-11-11.md)

## The idea

A program built from parts that meet in a few declared places is cheap to change. Edit the inside
of one part, leave its public side alone, and only that part's tests need to run. Two services
with a declared interface are the extreme case. The same holds inside one program when each part
has a public side and a private side.

So good architecture and cheap testing are the same property, and one number shows both.

**Test reach: the share of the test suite's time that a change needs.**

- Of a file: the tests AnaxiGraph would pick if that file changed.
- Of a part, meaning a folder of code that belongs together: the reach of its typical file.
- Of a repository: the reach of its typical merged pull request, replayed against one recorded
  run of the suite.

Low reach means separate parts and a short wait for CI. Rising reach means the parts are growing
together. The map, the advice and the test picking exist to bring this number down, and each
feature is kept or removed by whether it does.

## What the first measurement showed

Measured on 18 November on the private product repository of the real-repository plan: one
recorded run of its backend suite (632 test files) and its 70 most recent fix/feature pull
requests, replayed with the selection rule of commit `ce36d2e`. Nothing was executed again, and
nothing identifying that repository is recorded here.

**The typical pull request needs 69% of the suite** (mean 72%). None needs under 20%, and 23 need
practically all of it. No pull request left out a needed test.

Where the mean of 72% comes from:

| Layer | Adds |
| --- | ---: |
| Tests that executed or read what the pull request changed | 28% |
| Careful rules: new files, changed test helpers, files read as data | 11% |
| The top level of a file changed, so the tests of every file that uses it are picked too | 12% |
| Tests that start another program, picked for every change | 21% |

The top level of a file is what it sets up when it loads: imports, classes, constants, a new
function. Three of four source changes in those pull requests were of that kind.

Parts with walls are already cheap there, and shared files are what cost. This is the reach of the
typical file in a part, not counting the tests that start another program. About 3% is the lowest
possible value on that suite.

| Kind of part | Files | Change inside a function | Change at the top level | Files that other parts use |
| --- | ---: | ---: | ---: | --- |
| A sub-package with a narrow public side | 23 | 3% | 3% | 3 |
| A larger sub-package | 71 | 3% | 4% | 39 |
| A group of files in one flat folder that share a name prefix | 40 | 9% | 33% | 30 |
| The database model files | 30 | 3% | 41% | all 30, used by 234 files |
| The application's top-level files: settings, database session, entry point | 6 | 4% | 57% | 2, used by 249 files |

Two files stand out. One class holds all 237 settings; 13 of the 70 pull requests changed it, and
each of those needs the whole suite. The package file that imports every database model was
changed in 8, with the same result.

What five changes would do, each replayed on top of the ones above it. These are best cases: a
row assumes its change removes the whole effect.

| Change | Where | Typical pull request | Mean | Under 20% (of 70) |
| --- | --- | ---: | ---: | ---: |
| Today | | 69% | 72% | 0 |
| The recording sees inside tests that start another program | AnaxiGraph | 44% | 51% | 20 |
| Settings kept by the part that uses them, not in one class | the product | 37% | 46% | 20 |
| Top-level changes to model files stop reaching every user of every model | the product | 37% | 41% | 20 |
| Source files that Python opens behind the scenes no longer count as data a test read | AnaxiGraph | 31% | 39% | 25 |
| The flat folder of 260 files gets walls | the product | 27% | 32% | 27 |

Also replayed: picking by changed function instead of by changed file moves the typical pull
request from 69% to 67%. Most changes touch the top level of a file, so the function is the wrong
place to look.

Two conclusions. Work inside AnaxiGraph can take that repository's typical pull request from 69%
to about 40%. The rest depends on how the product is built, and that is the job the buddy exists
for: show where the walls are missing, and keep new code from leaning on what should be private.

## The plan

Three stages. Every phase has one number, a limit, and a point where the work stops and reports.

### Stage A: measure

**Phase 0. Clear the desk.** Commit or drop unfinished work. No new advice, review, workflow or
dashboard feature starts until phase 7 has shown which of them help.
Number: uncommitted work in the checkout, 0. Limit: one day.

**Phase 1. A recording that counts.** Record the product's backend suite once with the current
build: one pytest run, following the Python programs that tests start
(`ANAXIGRAPH_TRACE_CHILDREN=1`). Replay the same 70 pull requests.
Number: reach of the typical pull request, somewhere between 44% (every started program seen
into) and 69% (today). Needed tests left out must be 0.
Limit: one overnight run and one working day. If the single run does not finish, stop and report;
do not build another way of recording.

*Done on 18 November.* One pytest run recorded the suite in 69 minutes and followed all 2,197
Python programs its tests started. The build of that morning picked the whole suite for every
change on that recording. The causes were found and fixed the same day (`c6b0846`, `d7cbdb2`):
a module that started a program the recording could not see into stood for every test that ran
the module, and for the whole suite when coverage had not measured it; source that Python opens
to print a line of a traceback counted as data the test read; and code a test loads from outside
the suite's folders counted for nothing. A second recording was then made with the fixed
recorder, with coverage naming every folder the suite loads code from. The same 70 pull requests
throughout:

| | 16 November | Morning of 18 November | First recording | Second recording |
| --- | ---: | ---: | ---: | ---: |
| Picked for every change | 40% | all | 4% | 2% |
| Typical pull request | 69% | 100% | 53% | 47% |
| Mean | 72% | 100% | 56% | 52% |
| Pull requests under 20%, of 70 | 0 | 0 | 17 | 19 |
| Whole suite by rule | 2 | 70 | 12 | 10 |
| A needed test left out | 0 | 0 | 0 | 0 |
| Ten planted faults: a newly failing test file left out | 0 | | 0 | 0 |
| Ten planted faults: share of suite time picked | 50 to 82% | | 13 to 58% | 12 to 57% |

Of the 10, nine change a CI workflow or another shared setup file and one a dependency list.
`record-tests` now says what a recording is worth when it is saved: the share every change will
pick, the reason when that is everything, and the code the run loaded without measuring it.

*Later that day.* Four of the nine changed only a workflow that does not run the suite: one
that deploys, one that updates tools, one that builds another program. Such a workflow cannot
change how the job that runs the tests runs them. Inside a GitHub Actions job the selection now
asks which workflow the job belongs to (`GITHUB_WORKFLOW_REF`); only that workflow and the local
workflows it uses count as its setup, and any other workflow picks the tests that read it.
Anywhere else every workflow still counts. Same 70 pull requests, same recording:

| | Before | With the rule |
| --- | ---: | ---: |
| Typical pull request | 47.2% | 42.8% |
| Mean | 52.2% | 50.4% |
| Pull requests under 20%, of 70 | 19 | 20 |
| Whole suite by rule | 10 | 6 |
| A needed test left out | 0 | 0 |

Two of the four came down, one from the whole suite to 2%; the other two need everything for
the code they change. One fault was planted in such a workflow, a setting that a test of the
suite forbids there, and the whole suite run: one test file newly fails, the rule picks 2.4% of
the suite's time where every test was picked before, and that file is in the pick.

**Phase 2. The scoreboard.** A reach report from a saved record and the repository's history: the
repository's number, its parts ranked, the files that cost most (how often changed, times what
they pull in), and the largest what-ifs, each replayed. It starts in
`experiments/real_repository/` as an extension of `replay_tests.py`, outside the size budget. A
prototype produced the figures above; it is not in this repository because it carries the private
repository's layout.
Number: it reproduces those figures from the same recording.
Done when one command prints the report for the product and for this repository in under five
minutes. Limit: three days.

*Done on 18 November:* `experiments/real_repository/reach_report.py`. From the first recording
and the rule of its build it reproduces the figures above, in 204 seconds. On this repository,
with the record CI makes and the last 70 commits, it takes 30 seconds and says the typical commit
needs 97% of the unit suite: nearly every test goes through one application object, and one file
was changed in 20 of the 70 commits.

**Phase 3. Watch only.** In the product's CI: a nightly recording on the main branch; on each pull
request the whole suite still runs, and one extra step compares what AnaxiGraph would have picked
with what failed (`anaxigraph tests --ran`).
Number: failing tests outside the pick over at least 30 pull requests, 0.
Needs the maintainer's agreement, because it changes that repository's CI and its cost.
Limit: two days of work; after that it runs by itself.

*Opened as a pull request on the product on 18 November*, on the maintainer's word. A nightly
workflow records the suite; one step in the pull-request job reports the pick and any failing
test file outside it, and never fails the job. Both were run locally against a real result;
the pull request's own run is their first execution on the product's runners. That repository's
Python job takes 41 minutes today, and the step adds about two. The count towards 30 starts with
the first nightly record after it is merged.

*Merged the same evening*, on the maintainer's word. On the product's runner the recording job
took 58 minutes: 6,832 tests passed, the record was verified, and every change picks 2.2% of the
suite. The first record on the main branch was made that night, in 61 minutes, and the step's
lookup was then run by hand against it: an unchanged checkout needs no test, and an edit to one
function of one file picks 80 of 646 test files, 10% of the suite's time. The step itself first
runs with a record on the next pull request there.

### Stage B: guard and advise

Phases 4 and 5 can run in either order.

**Phase 4. Walls.** A part is a folder of code. Its public side is what its `__init__` exports or
`__all__` names, and a name with a leading underscore is private everywhere. The map already reads
this. New:

- a list of the parts, the public side each one really has today (what other parts use), and the
  reach-arounds: uses of something that is not on the public side;
- a ratchet: today's reach-arounds are written down as known debt, and only new ones are reported;
- `prepare_change` tells an agent the public side of the parts it is about to touch, and
  `validate_change` names any new reach-around. This is advice first; a project may make it a CI
  check.

Before building, spend half a day with Tach on the product. If it already declares and checks
parts well enough, AnaxiGraph reads its file instead of inventing one.
Number: new reach-arounds merged per week, 0 once it is switched on, with the known ones falling.
Limit: one week.

*First step, 18 November:* `experiments/real_repository/walls.py` lists the parts, the names
other parts use of each (its doors), the uses of private names across parts, and with `--base`
what a change adds to both. On the product's backend: 10 parts, 3,111 uses of one part by
another through 1,123 doors, and 25 uses of a private name. Twelve weeks earlier the backend had
half the source files, 547 doors and 6: about 1.6 new ones a week with nothing watching. On this
repository: 78 doors, none private.

Tach, tried on the product first: nine modules declared, synced and checked in under a second,
with JSON output. It declares and checks boundaries well, so `walls.py` reads its file where
there is one. One of its rules found the 5 private names that are imported by name; the other
12 are reached through an imported module, and 8 are names a module leaves out of `__all__`.
Following imports to pick tests, as its dependency map allows, picked 75% for the typical pull
request there and left out a test that had run or read a changed file in 30 of 70.

In the product, `validate_change` already reports a new use of another file's private name. The
doors of a part in the brief wait for room under the size ceiling.

**Phase 5. Pick by what changed in the interface.** Today a top-level change to a file picks the
tests of every file that uses that file. The map knows which names each file uses. So when only
some names changed, pick the tests of the files that use those names. This is the rule that makes
walls pay: a change to private names reaches nobody outside the part.
Number: mean reach on the replay. The bound is 12 points, from 51% to 39% after phase 1. It ships
only if the replay and the planted faults leave out no needed test and it gains at least 5 points.
Limit: three days to measure, before any product code is written.

*Measured on 18 November: not built.* `experiments/real_repository/interface_picking.py` replays
the rule. On the product, mean share of suite time: 56.1% today, 54.2% with the rule in its
careful form (1.9 points), 52.8% in a form that trusts decorators and base classes (3.3), 45.4%
in the best case. The bar is 5. The settings class, the core model classes, tables of data and
the file that wires the application together are used by name almost everywhere, so following
names changes nothing for them.

A third form was measured the same day, on the second recording. Where a change only adds or
alters plain data members of a class, such as a settings field with a default, it follows the
files that spell those members, in code or in data, instead of every file that uses the class.
It was first reported together with the form by name, as 7.2 points. Measured by itself that
evening, and then made careful, on the same 70 pull requests:

| Rule | Typical | Mean |
| --- | ---: | ---: |
| Today | 47.2% | 52.2% |
| By name | 44.1% | 50.2% |
| By name and by field (the first report) | 37.0% | 45.0% |
| By field alone | 37.0% | 48.6% |
| By field alone, careful about where a spelled name runs | 37.0% | 49.2% |
| By field alone, also following members read by a computed name | 47.2% | 50.9% |

By itself the field form gains 3.6 points, not 7.2, and in 6 of the 70 it no longer picks a test
that ran the changed file. It is also not safe as first written. The product reads settings
through `getattr` with a name taken from a table or built from parts in 36 places of 16 files,
and the tests of those files are a quarter of the suite. A form that follows only spelled names
cannot see such a read; following them as well leaves 1.3 points and nothing for the typical
pull request. *Not built.* The bar is 5.

Three faults had been planted in settings defaults, with the whole suite run for each:

| Changed default of a setting that is | Test files that newly fail | Today's rule picks | The field form picks | Left out |
| --- | ---: | ---: | ---: | ---: |
| read in one place | 0 | 100% | 4.6% | |
| named in a table of data | 0 | 100% | 5.4% | |
| read widely | 23 | 100% | 34.3% | 0 |
| read in six files and named in no test (planted that evening) | 14 | 100% | 6.0% | 0 |

Both faults that make tests fail are caught. Neither is read through a computed name; that
case is known from reading the code and was not planted, since the form is not being built.

**Phase 6. Advice aimed at the number.** The reach report's findings, said at the moment they
matter. In `prepare_change`: "this file is changed in one pull request of five, and each time the
whole suite is needed; put the new setting with the part that uses it." In a review: the three
changes that would lower this repository's reach most, each with its replayed effect. Advice that
cannot name its effect on the number is not given.
Number: reach of the typical pull request over the last 30 merged, on the product. Falling.
Limit: one week.

*First step, 18 November:* `prepare_change` carries `test_reach` for the likely owners and
`assess_plan` for the files a plan names: the share of the suite a change inside a function
would pick, the share for one at the top level, and the share picked whatever changes. The
numbers are the selection's own. On the product a brief with it takes 0.5 seconds. It took the
place of the co-change advice, which had not beaten keyword search. The sentence that names a
file's history ("changed in one pull request of five") is not written yet.

### Stage C: prove and switch on

**Phase 7. With and without the buddy.** The same model does the same tasks from the product's
history, once with AnaxiGraph and once without. About ten tasks. Scored on three things: the
task's own tests pass; the reach of the change it wrote and the reach-arounds it added; time and
money spent.
Number: tasks where the buddy's change is as correct and has lower reach, against the extra cost.
What does not move these scores is removed from the product. That is also how new work pays for
its lines.
Needs a spending limit from the maintainer. Not before phase 4.

**Phase 8. Switch it on.** Pull requests run the picked tests. The main branch and the nightly run
keep running everything, as the safety net.
Number: CI minutes per pull request, and failures that only the main branch caught, 0.
Not before phase 3 has 30 pull requests with nothing missed.

## When the whole thing is done

On the product repository:

- the typical pull request needs under 45% of the suite with no change to the product, and under
  30% once its settings, model and flat-folder changes are made;
- 30 pull requests in watch-only mode with no failing test outside the pick;
- no new reach-around merged for four weeks;
- the comparison shows the buddy's changes are as correct and have lower reach, at a cost the
  maintainer accepts.

And AnaxiGraph is still under its production ceiling and answers a brief in under a second.
The ceiling was 15,000 lines; the maintainer set it to 16,000 on 18 November, to be used only
where it is needed. Production is at 15,029.

## How the work is run

- One phase at a time, in order. A work order names the phase, its number and its limit, and ends
  with "stop and report".
- A number is reported as measured on a real repository, together with what was not run.
- New work starts in `experiments/`. It moves into the product by replacing something where it
  can; the ceiling is the maintainer's to move.
- A selection rule ships only when the replay and the planted faults leave out no needed test.
- Nothing is added to the product's repository or its CI without the maintainer's agreement.

## Decisions that are the maintainer's

| Decision | Needed for |
| --- | --- |
| Freeze new advice, review, workflow and dashboard features until phase 7 (agreed with the plan, 18 November) | Phase 0 |
| A nightly recording job and a watch-only step in the product's CI (agreed, and merged on the maintainer's word, 18 November) | Phase 3 |
| Which product changes to make: settings per part, model imports, walls in the flat folder. The settings split was asked for on 18 November; reading the code first showed most new settings there are named in tables of data and read through one object that 175 functions take, so a split would move little. The field form of phase 5 was meant to reach the same effect from the tool; measured by itself and made safe it gains 1.3 points, so it was not built either. No product change was prepared | The product's own reach |
| The production ceiling: 16,000 lines, used only where needed (18 November) | Any product code beyond 15,000 lines |
| A spending limit for the comparison | Phase 7 |
| A release | Whenever wanted; `main` is ahead of 1.0.0 |

## What exists already

- Bazel, Nx and Pants run only the tests that depend on what changed, from a dependency graph;
  Pants reads that graph from Python imports. They replace the build. AnaxiGraph leaves the build
  alone and goes by what the tests were recorded doing.
- Tach checks declared boundaries and public interfaces between Python modules. Phase 4 tries it
  before building anything.
- pytest-testmon picks tests from recorded coverage.

None of them tells an agent, while it is writing, where the walls are, or shows a team whether its
walls are getting better. That is the gap this fills.

## Not now

Picking by function (measured: 2 points). Test picking for the frontend suite. New benchmark
applications. More kinds of review. Tracking every finding through decision, implementation and
verification.

## Status

| Phase | State | Measured |
| --- | --- | --- |
| 0 Clear the desk | Done, 18 November | Uncommitted work: 0. The unfinished dashboard fix was committed with its tests (`d40002d`) |
| 1 A recording that counts | Done, 18 November | Typical pull request 43% (69% before), mean 50% (72%), 20 of 70 under 20%, nothing needed left out, ten planted faults none left out |
| 2 The scoreboard | Done, 18 November | `reach_report.py`: the product in 204 seconds, this repository in 30 |
| 3 Watch only | Nightly recording active | 22 November: 0 of 30 pull-request comparisons saved. The trial job needs the candidate reads report to verify a comparison |
| 4 Walls | First step done | `walls.py`. The product: 25 uses of private names across parts, 6 twelve weeks earlier |
| 5 Pick by what changed in the interface | Measured, nothing built | The bar is 5 points. By name: 2. By field: 3.6 by itself, 1.3 once it is safe |
| 6 Advice aimed at the number | First step done | `test_reach` in `prepare_change` and `assess_plan`, 0.5 seconds on the product |
| 7 With and without the buddy | Open; needs a spending limit | |
| 8 Switch it on | Open | |
