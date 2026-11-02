# Google prototype integration

Source: the maintainer's `hcekne/anaxi_test_google`, commit `8373f5b`, reviewed on
2 November 2026. Incorporate the useful interface and evaluation design into the
shared AnaxiGraph application.

## Selected changes

1. Add **Evals** beside Explore, Workflows and Make a change. Retain the prototype's
   comparison cards, progression through requests, and evidence inspector. Show the
   real local task/usage ledger and imported feature-drift reports. Distinguish
   reported measurements, missing measurements and incomplete qualification.
2. Preserve **ChronoWeave** and **AetherLoom** as versioned scenario specifications:
   initial design plus twenty requests each, inline probes at 7, 14 and 20, and
   their six proposed behavioral invariants. Make them discoverable/exportable
   through the existing experiment tooling. Freeze scenario identities for resume.
3. Turn the prototype's proposed failure categories into executable adversarial
   regressions for AnaxiGraph: dynamic dependencies, annotation invalidation,
   retrieval evidence, reuse of unchanged work, and unparsed/shared CI inputs.
4. Verify the dashboard in a real browser against a disposable repository and the
   real HTTP application. Exercise imported reports, missing data, pagination,
   invalid input, small screens and the existing workspace pages.

## Reuse boundaries

The prototype's Explore and workflow styling already closely follows this
dashboard. Keep the native assets and one Python Application; a second React build
and browser analysis engine would duplicate working interfaces and reasoning.

The prototype's engine simulates scans, model calls, timings and recommendations.
Its benchmark formulas assign results to each arm and its tests require AnaxiGraph
to win. Those numbers, fixed diagnoses and modularity scores are examples, not
measurements. Do not publish them as evidence or use them as acceptance criteria.
Imported report content remains supplied evidence and cannot prove authenticity.

The new scenario prompts need explicit wire contracts and evaluator-owned hidden
checks before a fair paid comparison. Keep them **specification only**, with no
results or comparative verdict. In particular, define numerical/CRC rules, resolve
tick-boundary quarantine semantics, and judge extension isolation without requiring
particular implementation filenames. Existing Switchboard campaigns keep their
current runnable behavior and independent post-history probes.

## Acceptance

- Dashboard actions use existing guarded HTTP APIs; viewing Evals starts no model
  jobs and executes no analyzed project code.
- Report comparisons retain safety/coverage qualification, show unknown cost as
  unknown, and do not calculate savings from invented or incomplete observations.
- All 42 requests and six inline probes survive transfer without generated arm
  metrics. Attempting to run an unqualified scenario fails before a model starts.
- Regression fixtures test product behavior, with no assertion that one arm must
  win. Existing benchmark behavior and resume safety remain covered.
- Browser, Python, experiment, lint, type, package and source-budget checks pass.
  Keep the existing 13,500 runtime-line ceiling; record actual size after changes.

Commit and push to `main`, then verify CI. This integration does not publish a new
PyPI release or restart unrelated services.
