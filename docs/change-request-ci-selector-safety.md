# CI selection must preserve consumer obligations

The November 2026 Switchboard v4 trial retained 108/108 hidden acceptance checks
but AnaxiGraph's selected tests covered only 0.6857 of observed consumer execution
pairs at request 20: 5,466 of 17,390 pairs were omitted. Catching ten sampled faults
did not establish that the other consumers could be skipped. Quality takes
precedence over test savings, tokens or elapsed coding time.

The failure had two parts: validation could report passing receipts alongside a
consumer gap, and the coding loop never required that gap to be resolved.

## Implemented contract

- `validate_change` returns `validation_status`, `completion_allowed`, and
  `ci_selection.required` / `ready`. Passing local receipts cannot override omitted
  consumers, missed reference-detected faults, or a failed qualifying suite.
- Known `ci.json` / `anaxigraph/ci-policy.json` configurations require current CI
  evidence for relevant code or configuration changes, including configuration
  removal. Callers can require it explicitly for other selector arrangements.
  Documentation-only changes retain scoped behavior.
- Required evidence matches source, tests, configuration, the caller's current
  environment fingerprints, a nonempty successful suite, and the actual changed
  path set. Missing, invalid, stale, unbound or incomplete evidence is not success.
- Audit individual production files and the combined change. Recompute required
  consumers from observations; do not trust a supplied success label. Unknown
  configuration or unobserved changed inputs conservatively retain all tests.
- Raw supplied check outcomes remain visible. The combined behavioral status is
  failed or unresolved when CI safety is blocked or incomplete. CLI validation
  returns nonzero for a required gate that does not allow completion.

This deliberately uses conservative certification for configured selectors. The
static graph cannot prove that arbitrary dynamic selector helpers are independent.
It does not make directory proximity an ownership or verification contract. A
declared contract-test substitution alone cannot waive observed consumers; widen
selection until a replacement has independently established its obligations.

The Application never executes analyzed source, project tests or selector commands.
An external collector/CI process runs them and supplies attributed evidence.
Use its strict check mode as a required CI step; merely displaying a JSON finding
cannot force an unrelated agent to obey it.

## Coding experiment feedback loop

All arms receive the same deterministic verification and bounded repair budget.
After initial coding, collect coverage once, actual combined selection and fault
observations; publish the current compact report. The treatment also validates
through AnaxiGraph against its original pre-edit baseline. A fresh coding session
receives concrete omissions and must repair the selector, then recollect evidence.
Keep each attempt, its result and its resource use. An unresolved chain stops and
retains pending work rather than advancing to the next request. Hidden acceptance
checks remain evaluator-owned; do not reveal their source or expected answers.

CI-only repair preserves existing test obligations. Count all repair coding tokens
and time; report common mechanical verification separately from AnaxiGraph analysis.
This changes the experiment protocol: do not mix the new runs with v4 replicates.

## Oracle corrections

An empty selection previously invoked pytest with no targets, inadvertently running
the full suite and potentially receiving false mutant-detection credit. Empty
selection now executes zero tests and cannot catch a reference-detected fault.

The Notes probe counted upstream health calls made before its quota test. Measure
the allowed invocation's outbound-call delta, then separately require no additional
calls after quota or disablement rejection. Rechecking disposable copies of all
three v4 implementations passes the corrected Notes tests; archived results stay
unchanged. This correction does not qualify any unsafe CI selector.

Regression fixtures exercise omitted consumers, multi-file selection, stale inputs,
environment mismatch, missing observations, full-suite failures, empty selections,
CLI exits, all interfaces and repair-loop accounting without paid models.
