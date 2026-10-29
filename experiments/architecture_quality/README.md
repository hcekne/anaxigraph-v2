# Does architectural advice improve the next change?

The primary **discovery track** asks whether Patterns and Fresh Eyes help agents find
useful architectural opportunities while delivering ordinary product changes. It measures
later behavior, state ownership, failure handling, verification scope and blinded design
judgment. Counting recommendations or accepting the implementer's self-report cannot
establish usefulness. No discovery effectiveness result is bundled here.

`--track discovery` uses a workshop reservation product with catalog/pricing, capacity,
payment effects, confirmation delivery and operator reporting tangled in an ordinary
application. The initial task is a normal group-booking/reporting feature brief plus a
generic quality goal. It prescribes no components, extraction, pattern or target layout.
Subsequent product tasks are disclosed only when their fresh agent starts. They exercise
changed provider behavior, cancellation/refund/retry ownership and shared settings/schema
semantics. Independent sequence checks verify atomicity, idempotency, restartable state,
provider calls and reporting; they do not import a preferred implementation.

The discovery track permits a behaviorally correct monolith and a behaviorally correct
separated design to qualify automatically. Its `qualified` flag establishes behavior and
CI safety only. Source observations, voluntary local verification targets, actual later
patches and the blinded rubric assess architecture. Merely declaring a boundary or test
target earns no architectural score; a conservative full check selection is safe. There
is no required number of components and no gold module name, directory tree or LOC target.

The **guided track** (`--track guided`, retained as the CLI compatibility default) is a
separate evaluator/orchestration qualification. Its explicit compute/delivery contracts
make it useful for proving the oracle catches coupling and under-testing. It measures
execution of an instructed refactor, **not discovery of useful architecture**. Do not
present guided fixture success or its automatic isolation flag as discovery evidence.

Every pair starts from the **same source**, with a byte-for-byte baseline digest. The
guided track's checkout authorization, notification dispatch and report export are
separate behavioral scenarios. Each has a refactoring milestone followed by a policy extension,
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

Both arms receive the same source-independent quality charter and generic quality goal.
Full frozen reviews stay private: treatment prompts contain only review IDs, kinds and
inspection references. Agents scan/prepare, browse bounded listings and inspect relevant
reviews progressively; inspecting every review is not required. Screening alone does not
count as material advice consultation. Active generic-review controls likewise receive
metadata for supplied note files, rather than every review body pasted into their prompt.
The current protocol records this delivery/balance explicitly. Earlier full-frozen-advice
runs are orchestration qualification, not evidence for ordinary progressive use or a
charter-matched effectiveness comparison.

Controls explicitly forbid AnaxiGraph CLI/package/MCP/HTTP access and saved analyses;
ordinary source inspection, grep and local checks remain available. The harness records
observable AnaxiGraph MCP calls, recognized CLI/Python/record/HTTP commands and candidate
package imports as control contamination. Active review preparation stops on such flags;
coding outcomes retain the flags for interpreting the pair. This is an observable protocol
audit, not an adversarial filesystem/network sandbox or proof that no hidden access occurred.

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

The synthetic fixtures exercise the discovery task chain and the guided calibration
without calling a model, paid API or AnaxiGraph workflow:

```bash
uv run python -m experiments.architecture_quality.run \
  --synthetic --track discovery --state /tmp/architecture-discovery-qualification
uv run python -m experiments.architecture_quality.run \
  --synthetic --track guided --state /tmp/architecture-oracle-qualification
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
  --track discovery --scenarios workshop \
  --state "$HOME/.local/state/anaxigraph/architecture-quality-reviewed-control" \
  --control-mode review --replicates 3
```

`--scenarios checkout --stages 1` is a workflow/coding smoke, not the full architectural
outcome experiment. `--call-timeout 1800` changes each coding/review invocation and each
AnaxiGraph worker timeout symmetrically. There is no total-run deadline. Do not run
another experiment into an existing state directory: frozen trials cannot silently
resume against a new source or model. `--runtime-source-root /path/to/anaxigraph` records
the exact installed runtime source. An explicit `--resume-preparation` can recover an
incomplete live preparation for the next unstarted pair, with identical configuration,
client, executable, recorded runtime source and seed trees. Completed pairs and blind
exports are verified and retained; their coding never repeats. Recovery retains the
failure, reruns preflight, counts administrative/retry costs and reuses accepted analyses.
Cache reuses remain separate from actual model-call receipts. Any coding artifact in an
unfinished pair, or active control reviews already started there, requires a new trial.
No repository commits, pushes or releases occur.

A custom `--agent` executable is always classified synthetic, even with a live-shaped
invocation. The standard client records its resolved path and reported version; these
are process provenance, not signed model/provider attestation. The configured runtime
is trusted experimental instrumentation. Pin its source and retain its version/hash.

## What is checked

Both evaluators stay outside the worker checkout and run in separate subprocesses.
The guided evaluator generates hidden randomized examples and edge cases, verifies independent compute
and delivery entrypoints with injected providers, checks validation before effects,
exact side effects, exception behavior, input immutability, backward-compatible provider
behavior, and default shared configuration/schema semantics. A candidate's passing
tests cannot substitute for those checks. Each declared local test target is also
executed with the neighboring implementation unavailable, including fresh package
initializers and relative imports. Empty/missing tests fail. Tests cannot rewrite the
evaluated checkout after hidden behavior checks and still qualify. Contracts must be
present; their substantive quality remains a separate blinded human judgment.

In guided qualification, components can be ordinary functions in one module, modules or packages in one process.
There is no gold directory tree, package quota or LOC score. The small public contract
requires declared replaceable compute and delivery entrypoints. AST evidence verifies
transitive source inputs, component imports, direct calls across co-located entrypoints,
and cycles; independent behavioral execution provides separate evidence. Dynamic
imports, reflection, global aliasing and every possible architectural smell are not
fully established by that static analysis. Treat this as a bounded Python benchmark.

Guided CI selection is tested against actual changed paths and independent transitive source
inputs, not an agent's scope assertion. Local implementation changes can earn owner
plus integration checks. Changed behavioral contracts, even without signature changes,
widen scope to consumers. Shared configuration, schema, build files, CI selection,
ownership metadata and unknown inputs must retain all checks. A coupled adapter must
include its compute consumer; an isolated adapter can select delivery plus integration.
Required tests are retained. Discovery CI likewise retains existing obligations and
tests shared-state/configuration widening, accepting broad fallback when ownership is
unknown. Unsafe under-testing disqualifies either track.

Behavioral success, boundary observations, independent verification, safe CI scope,
later-change effort and human contract quality remain separate outcomes. The
guided `qualified` flag requires behavior, all declared tests, independent component tests,
boundary constraints and CI safety to pass; no smaller code/test scope compensates
for broken behavior. Discovery qualification requires behavior and CI safety; architectural
quality remains a blinded human judgment supported by later changes. Changed lines are
descriptive effort data, never a quality score.
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
making broad claims. One small stateful product and three tiny guided domains do not
represent a large product or the variety of real maintenance work.

Guided qualification tests prove that behaviorally passing coupled and modular fixtures are
distinguished, equally sized layouts are not the score, co-located functions are accepted,
buggy code can pass its own tests and still fail, understated inputs/CI scopes fail, and
the complete synthetic automation writes reviewable results. **Synthetic fixture success
is evaluator evidence only.** Actual advice usefulness requires live paired task chains,
observed successful tool delivery, independent behavior checks, blind review, and sufficient
replication to characterize variance. No such effectiveness result is bundled here.
Discovery calibration additionally accepts both monolithic and separated behaviorally
correct designs, rejects state/effect/retry bugs hidden by candidate examples, and carries
each candidate's real opaque persisted state across task edits. It does not impose a gold
state schema or grant architecture points for file count or declarations.

The [live qualification record](../../docs/evidence/architecture-outcome-qualification.json)
contains one completed guided pair: all four tasks passed in both arms, with successful saved-advice
consultation. That run used the earlier full-inline, unmatched-charter protocol. Its large context
and latency totals motivated the compact current protocol; it establishes working orchestration,
not discovery effectiveness or an architecture/CI advantage.

Codex launch and MCP approval settings follow the [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
