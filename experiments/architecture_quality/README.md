# Does architectural advice improve the next change?

This experiment measures coding outcomes after Patterns and Fresh Eyes, rather than
counting recommendations or accepting the implementing agent's self-report. It is a
small controlled benchmark, not a result establishing that AnaxiGraph works.

Every pair starts from the **same coupled source**, with a byte-for-byte baseline
digest. Checkout authorization, notification dispatch and report export are separate
behavioral scenarios. Each has a refactoring milestone followed by a policy extension,
an independently replaceable provider change, and a shared configuration/schema change
that keeps public signatures unchanged. Future tasks are disclosed only at their turn.
Every task uses a new ephemeral coding context with `gpt-6.1-sol`, `xhigh`, the same
per-call timeout (default **none**), the same four opportunities, and no extra repair
turns. The two arms run concurrently; launch order and blind labels are randomized.

The coordinator runs mapping, Patterns (including architecture and CI scouts), and
Fresh Eyes before implementation. All stages explicitly select the same model and
effort, without fallback upgrades. It freezes actual reviews, baseline identity,
configuration, model receipts and Fresh Eyes request packets before either arm codes.
Fresh Eyes runs through AnaxiGraph's tool-free, fresh-context workers; its packets are
audited to contain only behavior, the source-independent charter and blind-design
instructions. The implementer can accept or reject advice. A review is counted as
consulted only after a successful MCP inspection whose result digest matches the
frozen source. Dispositions are joined to those reviews and actual changed files.
Those checks establish source evidence, **not semantic adoption**; the blind patch
review must determine whether the resulting code implements the stated advice.

The capability preflight uses the real Codex client and explicit AnaxiGraph MCP
configuration. It requires successful `scan`, `prepare_change` and `inspect` replies,
no failed calls, no edits/shell work, and independent server ledger evidence of a task
and supplied context. A completed turn, attached server, approval-blocked call, or
synthetic event cannot qualify delivery. No external/reusable preflight-proof file is
accepted. Incomplete analyses stop implementation. A preflight does not prove the
subsequent workflows or coding tasks will succeed.

Two control modes answer different questions:

- `--control-mode normal` measures ordinary coding versus adding the AnaxiGraph
  routine. The analysis costs are an additional treatment cost, reported separately.
- `--control-mode review` gives the control fresh generic source reviewers, using
  the same quality brief, architectural requirements, model and effort. By default
  it matches the number of observed AnaxiGraph model-call opportunities; an explicit
  `--control-review-calls N` predeclares a different budget. These reviewers use
  ordinary source inspection/grep and cannot edit. Their advice is frozen before
  coding too. This helps distinguish structured tool advice from additional thinking.

Neither mode promises equal actual tokens or total inference cost. Compare measured
coding **and analysis** usage, with missing token/cost receipts left null. Matching
opportunities is not matching tokens. Do not claim cost efficiency from these runs
without adequate total-usage/billing evidence. Run both control modes as separate,
predeclared experiments; do not choose the favorable comparison after seeing outcomes.

## Run

The synthetic qualification exercises all three pairs and all four task stages without
calling a model, paid API or AnaxiGraph workflow:

```bash
uv run python -m experiments.architecture_quality.run \
  --synthetic --state /tmp/architecture-quality-qualification
uv run pytest experiments/architecture_quality
```

Live execution is opt-in. Use a fresh state directory outside any candidate repository:

```bash
uv run python -m experiments.architecture_quality.run \
  --live --runtime "$PWD/.venv/bin/anaxigraph" \
  --state "$HOME/.local/state/anaxigraph/architecture-quality-preflight" \
  --preflight-only

uv run python -m experiments.architecture_quality.run \
  --live --runtime "$PWD/.venv/bin/anaxigraph" \
  --state "$HOME/.local/state/anaxigraph/architecture-quality-reviewed-control" \
  --control-mode review --replicates 3
```

`--scenarios checkout --stages 1` is a workflow/coding smoke, not the full architectural
outcome experiment. `--call-timeout 1800` changes each coding/review invocation and each
AnaxiGraph worker timeout symmetrically. There is no total-run deadline. Do not run
another experiment into an existing state directory: frozen trials cannot silently
resume against a new source or model. `--runtime-source-root /path/to/anaxigraph` records
the exact installed runtime source. An explicit `--resume-preparation` can recover an
incomplete live preparation only before any coding artifact exists, with identical
configuration, executable, recorded runtime source and both seed trees. It retains the
previous failure, reruns preflight, counts administrative/retry costs, and reuses accepted
analyses through the existing workflow runner. Cache reuses are retained separately from
actual model-call receipts. This never grants another coding or repair turn. Active
control reviews already started and later coding interruptions require a new trial.
No repository commits, pushes or releases occur.

A custom `--agent` executable is always classified synthetic, even with a live-shaped
invocation. The standard client records its resolved path and reported version; these
are process provenance, not signed model/provider attestation. The configured runtime
is trusted experimental instrumentation. Pin its source and retain its version/hash.

## What is checked

The evaluator stays outside the worker checkout and runs in separate subprocesses.
It generates hidden randomized examples and edge cases, verifies independent compute
and delivery entrypoints with injected providers, checks validation before effects,
exact side effects, exception behavior, input immutability, backward-compatible provider
behavior, and default shared configuration/schema semantics. A candidate's passing
tests cannot substitute for those checks. Each declared local test target is also
executed with the neighboring implementation unavailable, including fresh package
initializers and relative imports. Empty/missing tests fail. Tests cannot rewrite the
evaluated checkout after hidden behavior checks and still qualify. Contracts must be
present; their substantive quality remains a separate blinded human judgment.

Components can be ordinary functions in one module, modules or packages in one process.
There is no gold directory tree, package quota or LOC score. The small public contract
requires declared replaceable compute and delivery entrypoints. AST evidence verifies
transitive source inputs, component imports, direct calls across co-located entrypoints,
and cycles; independent behavioral execution provides separate evidence. Dynamic
imports, reflection, global aliasing and every possible architectural smell are not
fully established by that static analysis. Treat this as a bounded Python benchmark.

CI selection is tested against actual changed paths and independent transitive source
inputs, not an agent's scope assertion. Local implementation changes can earn owner
plus integration checks. Changed behavioral contracts, even without signature changes,
widen scope to consumers. Shared configuration, schema, build files, CI selection,
ownership metadata and unknown inputs must retain all checks. A coupled adapter must
include its compute consumer; an isolated adapter can select delivery plus integration.
Required tests are retained. Unsafe under-testing disqualifies the result.

Behavioral success, boundary violations, independent verification, safe CI scope,
later-change effort and human contract quality remain separate outcomes. The
`qualified` flag requires behavior, all declared tests, independent component tests,
boundary constraints and CI safety to pass; no smaller code/test scope compensates
for broken behavior. Changed lines are descriptive effort data, never a quality score.
CI weights are explicitly **synthetic equal units**. Actual local subprocess timings,
coding latency, model usage and unknown costs are recorded separately. These are not
measured production CI savings or live application latency.

## Review and interpretation

`run.private.json`, raw tool/agent logs, frozen source/advice, task baselines and
`blind-map.private.json` contain private experimental artifacts. Keep them private.
`comparison.json` records separate paired stage outcomes and coding/analysis usage,
including known and missing observations. `blind-review/candidate-*` contains code,
task changes and independent outcomes without arm labels, advice or adoption claims.
Give reviewers only those bundles and [the rubric](rubric.md); hold the assignment map
until their judgments are frozen. If source prose inadvertently reveals treatment,
record that unblinding rather than asserting perfect reviewer blindness.

The randomized checks are withheld from worker prompts/checkouts, but the benchmark
implementation is public. This is not a security sandbox against a deliberately cheating
agent that reads arbitrary host files. Use separate containers/users or a private held-out
scenario set for adversarial evaluation, and pre-register additional unseen domains before
making broad claims. Three tiny arithmetic/effect domains do not represent a large product.

Qualification tests prove that behaviorally passing coupled and modular fixtures are
distinguished, equally sized layouts are not the score, co-located functions are accepted,
buggy code can pass its own tests and still fail, understated inputs/CI scopes fail, and
the complete synthetic automation writes reviewable results. **Synthetic fixture success
is evaluator evidence only.** Actual advice usefulness requires live paired task chains,
observed successful tool delivery, independent behavior checks, blind review, and sufficient
replication to characterize variance. No such effectiveness result is bundled here.
