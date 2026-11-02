# Dashboard evaluation checks

The native Evals workspace uses the existing guarded HTTP application. Its local
ledger is real retained usage evidence; imported feature-drift reports remain
supplied reports. Viewing either starts no model jobs. Unknown metrics are not
converted into zeroes, and missing observations leave chart gaps.

Run dependency-free import validation tests (also in CI):

```bash
node --test experiments/dashboard_evals/validation.test.cjs
```

Run the browser smoke with an existing Playwright installation and Chromium:

```bash
NODE_PATH=/path/to/node_modules node experiments/dashboard_evals/browser.cjs
```

The smoke launches a disposable loopback HTTP application using the checkout's
`.venv`. Providers return explicitly synthetic test answers; no model CLI runs.
It checks all four dashboard pages, saved progress/reviews, change baselines,
ledger pagination/filtering/export, report metrics and qualification, unsafe labels,
invalid/oversized imports, desktop/mobile layout and browser errors. Temporary
repository state is removed when finished. Screenshots, downloaded JSON, a server
log and a receipt remain under `~/.tmp/anaxigraph-dashboard-*`; set
`ANAXIGRAPH_SMOKE_OUTPUT` to choose another output parent.

Playwright and its browser libraries are optional developer tools, not package
dependencies. The browser smoke is opt-in; it does not add a slow pre-push gate.
