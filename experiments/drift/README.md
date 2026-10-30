# Long-horizon capability, CI and delivery drift

The [feature-drift campaign](../feature_drift/README.md) measures a product growing through
20 requests and three later probes with bare, charter and AnaxiGraph coding arms. This companion
campaign concentrates on retained compatibility, executable check selection and release evidence.
Keep their results separate: neither the reference campaign nor its synthetic cost model is a
substitute for the feature campaign's live coding outcomes or blinded architectural assessment.

This experiment extends the existing four-task architecture qualification with a **26-issue
retained product history**. A generated notification product starts with working delivery,
dynamic integration routing, authorization, persisted idempotency receipts, an event outbox,
separate API/MCP adapters and declared verification. One private adapter dependency makes the
starting source deliberately coupled. The deterministic reference repairs it in the first issue;
live agents receive ordinary requirements, not the location or desired extraction.

The benchmark does not prescribe an internal directory tree, component quota, class hierarchy,
service topology or source size. Its published Python surfaces are `solution.execute(...)`,
`providers.<adapter>.deliver(text)` and integration facades; implementations behind these surfaces
may move. The independent-adapter contract is tested with neighboring adapter implementations
unavailable. That is a bounded import/behavior probe, not a universal architecture verdict.

## Two separate tracks

**Deterministic qualification** uses reference edits, real subprocess checks, concrete faults,
retained opaque serialized state and immutable local build identities. It calls the production
`anaxigraph.analysis.ci_policy.plan_ci` on the generated accepted policy; it does not implement a
second selector. Reference success validates the evaluator and declared-input scenarios. It does
not establish that a coding model discovers the right architecture or that production CI is faster.

```bash
.venv/bin/python -m experiments.drift.campaign qualify \
  --state /tmp/anaxigraph-drift-qualification
.venv/bin/pytest tests/test_drift_benchmark.py tests/test_drift_adversarial.py
```

The state directory must be new and outside this checkout. `qualification.json` is the compact
report; `episodes/` retains actual plans, executed check receipts and faults. A failed reference,
missing verification obligation, invalid fault control or artifact/recovery failure returns a
nonzero CLI exit status. CI runs the default 12-adapter, 48-integration profile once. A partial
`--episodes 2` run is a rehearsal, never a full-history result.

Generated scale remains configurable without committing thousands of fixture files:

```bash
.venv/bin/python -m experiments.drift.campaign qualify \
  --components 110 --integrations 1000 --state /tmp/anaxigraph-drift-scale
```

This runs substantially more real checks and subprocesses. Time depends on the machine. Report
scale and runtime alongside results; do not extrapolate the small smoke's latency to this profile.

**Live paired coding** is explicit and opt-in. It reuses the existing architecture experiment's
Codex launcher, fresh ephemeral contexts, disabled external integrations, AnaxiGraph MCP preflight,
workflow advice preparation, raw client logs, tool parsing and observed control-isolation audit.
Future coding defaults are `gpt-6.1-sol` / `high`; the existing mapping/review experiment profile
remains `gpt-6.1-sol` / `xhigh`. Product semantic-analysis defaults are not changed by this experiment.

```bash
.venv/bin/python -m experiments.drift.campaign live \
  --runtime "$PWD/.venv/bin/anaxigraph" \
  --state /tmp/anaxigraph-drift-live-pair
```

**This command launches real model work.** There are 52 coding opportunities in a full pair,
plus preflight and treatment mapping/Patterns/Fresh Eyes at preparation and every eight issues.
Those analyses can add many model calls. No live run is implied by committing this harness or by
its mocked-transport tests. `--call-timeout N` optionally applies the same per-call timeout to both
coding arms; the default is no deadline. There is no extra repair turn or favorable rerun.

Both arms start from identical source and retain their actual subsequent source. Task order is
fixed; arm launch order is randomized from the recorded seed. Treatment uses progressive AnaxiGraph
MCP guidance and current scan/prepare/assess/validate. The control uses ordinary inspection and
checks, with AnaxiGraph access prohibited and observable contamination audited. Treatment review
metadata is supplied rather than full advice pasted into every coding request. Review consultation
and semantic adoption require examination of retained calls and patches; they are not inferred from
the model saying it used advice. The normal control has no matched additional review budget: report
all treatment analysis cost separately rather than claiming equal total resources.

`run.private.json` retains requested model/effort, frozen source and evaluator identities, client
identity, each issue, actual model/tool receipts, tokens when reported, source changes, behavior,
CI selection and local release outcomes. Raw events remain in `evidence/`. Client configuration and
reported receipts are process provenance, not signed proof of which provider/model executed.
Unknown tokens and dollars stay unknown. Tests exercise the driver with mocked model transport and
real two-stage acceptance; that is orchestration qualification, not live model performance.

## External agents

An external runner can request and submit one issue at a time without using the built-in client:

```bash
.venv/bin/python -m experiments.drift.campaign export --state /tmp/anaxigraph-drift-external
# Run each agent only in its treatment/ or control/ candidate tree, using TASK.json.
# Each agent writes submission.json after implementing the issue.
.venv/bin/python -m experiments.drift.campaign grade --state /tmp/anaxigraph-drift-external
```

Only the current and earlier task requirements enter `TASK.json`; expected scopes, reference
edits, hidden probe data, categories and future tasks remain outside candidate trees. The strict
proposal is exactly `{selected:[check IDs],deploy:[artifact IDs],unknowns:[strings]}`. Unknown fields,
duplicates, fabricated check IDs and self-reported pass evidence are rejected. `grade` records both
outcomes before exposing the next issue, and returns nonzero for any failed arm. A failure remains
in the history; the candidate receives no reference repair. Export/grade does not itself deliver
or prove AnaxiGraph treatment: an external runner must separately freeze and retain tool delivery
and model/runtime evidence before making a treatment-effect claim.

The coordinator's private state and oracle must be inaccessible to worker processes. The public
source repository is not a security boundary: the built-in workspace sandbox and process isolation
do not prove a hostile agent cannot read arbitrary host files. Use separate containers/users and
private unseen cases for adversarial evaluation. Freeze a protocol and replicate matched complete
histories before claiming model quality or a causal architecture advantage. Within-history issues
are dependent observations, not 26 independent trials.

## Qualification recorded on 30 October 2026

The [frozen-source evidence record](../../docs/evidence/drift-qualification-2026-10-30.json)
contains a successful full 26-issue reference run at 12 adapter slots and 48 integration facades,
plus 24 passing drift regression tests. Empty selection and stale evidence were rejected;
always-all verification remained safe with zero selection savings. The existing product and
experiment suites passed another 678 and 128 tests respectively. No live coding comparison ran.

For the new API and new MCP additions, the reference selected 6 of 25 check targets and changed
2 of 13 artifact identities: the new adapter and the host. A separate 110-slot/1,000-integration
generation, planner and artifact-scope check selected 17 of 221 targets and changed 2 of 111
artifact identities for an API addition. The larger check did not run the full behavioral history.
These are bounded synthetic workload observations, not production CI speedup measurements.

## What the sequence exercises

The issues cover private API improvement, API reply migration independent of MCP, tenant auth,
dynamic routing with old receipts, character-to-byte validation, old pending events under a new
wire envelope, real receipt representation migration, timeout-unit changes, shared Unicode
normalization, private MCP work, conflicting idempotency retries, acknowledgement semantics,
capability protocol versions, historical rerouting, shared roles, token expiry, persisted retry
limits, provider retirement, platform inputs, authorization audience, another private adapter
change, event replay, registry fallback, release/recovery identities, and **adding a new API and
a new MCP adapter through the same capability contract**.

Every issue runs independent probes of errors and observable effects as well as happy paths.
Failed sends are checked against populated state, so clearing old receipts cannot pass. Receipts
and unsent events survive source edits and JSON restoration. Earlier pending events are flushed
from a copy with the current envelope to detect migration loss without imposing a state schema.
Old successful requests stay side-effect free under later routing and authorization changes.
Provider/consumer checks exercise actual dynamic registration, not just import graph edges.

## Safety before efficiency

The coordinator runs the full independent oracle and the actual selected declared verification.
The published `checks.py` adapter and canonical commands are immutable verification capabilities;
the evaluator binds their external packet and runs them with an isolated interpreter. Candidates
may improve declared inputs, but changing commands to `true`, deleting checks, changing the adapter,
omitting the retained gate, or supplying a fake pass cannot earn credit. Public local smoke checks
are explicitly weaker than the external evaluator and cannot replace it.

A concrete runtime fault is then applied at the current published capability boundary. The full
oracle must fail on it. Actual failed checks establish the evaluator-owned required set; selected
declared commands are also executed on the fault. No expected set is copied from the planner being
evaluated. Missing required checks in **either the planner output or executed proposal**, omitted
planned obligations, failed/omitted gates, unresolved ownership, declared unknowns, candidate writes
during verification or stale evidence remove efficiency credit. Correct behavior does not excuse
unsafe selection, and running extra checks manually cannot conceal an unsafe automatic planner.

The qualification executes empty-selection, always-all and stale-evidence controls. Broad checks
are safe and have zero selection savings. Coupled-consumer, no-op-command, state-loss, private-helper
refactor, relative-import, hidden-input, bad-artifact and empty-rollback counterexamples have focused
regression tests. Passing bounded fault probes does not establish complete test coverage.

`observed_work` records actually executed local evaluator check counts and durations. The separate
`synthetic_cost_model` uses equal check units; it is **not measured production CI money or latency**.
No failed step earns efficiency credit, and later repair does not erase earlier failure.

## Builds, deployment and recovery

The packaging contract is independent adapter packages plus one host runtime. Adapter artifact
inputs contain the public adapter, actual transitive local imports, package initializers, its
capability metadata and pinned platform. Relative imports are resolved. The host contains routing,
authorization, shared data/configuration, event/state contracts and integration facades. Registering
one new adapter can deploy that adapter and the host without rebuilding unrelated adapter packages.
Prose, check declarations and saved AnaxiGraph analysis do not become deployment inputs.

Artifacts have immutable input/runtime digests tied to the exact externally checked candidate.
The release ledger requires every changed artifact, exact tested bytes and the prior successful
artifact identities. Recovery executes the complete previous contract oracle against the preserved
previous source, with its corresponding opaque state and exact source identity; an empty/fabricated
rollback check list fails. Failed coding, verification or release never promotes the release
checkpoint. The next CI comparison still includes unpublished changes, and rollback remains pinned
to the last successful release rather than the last attempted candidate.

This is an executable **local artifact and release ledger simulation**. It neither builds a real
container image nor exercises a hosting provider, production secrets, traffic canaries or networked
rolling deployment. Dynamic dependency completeness beyond the generated registry and declared
resource model remains outside its assurance. Those limits must remain attached to the report.
