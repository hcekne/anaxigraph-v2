# V2-first usefulness experiments

Prepared on 29 October 2026. Start with V2, then freeze the protocol before testing an
external project. This is an exploratory campaign, not a promise of an architectural benefit.
Existing live discovery results found no clear advantage and higher assisted coding effort;
see the [recorded cycle](README.md#live-discovery-cycle--29-october-2026).

## 1. Owner finding on real V2 history

The completed no-model replay at V2 revision `54dfb518e9c7c7efa5308fb366dc0ee98bc4ef52`
uses twelve recent qualifying commits, each evaluated from its parent. Eleven have an
existing-file owner label. AnaxiGraph and grep each hit an owner in the top three on **7/11**;
first-choice hits are **2/11** for AnaxiGraph and **4/11** for grep. This does not show an
owner-finding advantage. The [evidence record](../../docs/evidence/usefulness-setup-2026-10-29.json)
retains the complete summary, source identities and selected commits.

```bash
uv run python experiments/owner_replay/replay.py \
  --source "$PWD" --commits 12 --mode structural \
  --workdir /home/hcekne/operations/anaxigraph-usefulness-new-replay
```

Commit messages are task proxies and changed paths are incomplete ownership/impact labels.
This is development evidence, not held-out evaluation or an agent coding outcome. Do not
tune retrieval on these cases and then call a rerun independent validation. Structural mode
makes no model calls; mapped mode is a separately budgeted experiment.

## 2. Fresh-agent coding on V2 itself

Use a pinned V2 archive or detached worktree per arm outside the canonical repo. The current
candidate task chain is below; it has **not been run or independently qualified**. Before
launching, freeze private acceptance checks covering the listed behavior and prove that
they fail on the baseline for the intended missing feature. Give both arms the same current
task and existing requirements. Keep future tasks and private checks outside candidate trees.

| Stage | Product request | Acceptance to freeze before coding |
| --- | --- | --- |
| A | Filter the module inventory by a case-insensitive literal substring of relative path. Missing/empty query retains all modules. Offer the same optional query through Application, CLI, MCP and HTTP. | Filter the complete inventory before sorting and paging; correct filtered total and next offset; no duplicate/missing pages; unchanged default calls; transport parity; reject invalid input consistently. |
| B, disclosed only later | Add optional annotation-status filtering: current, stale or missing. Combine it with path filtering. | Combined filtered totals/paging; modules with no annotation count as missing; status changes after a source edit; invalid status rejection; all four interfaces agree; stage-A behavior still passes. |

Use ordinary source/search/tests for the control. Start with structural `scan`, `prepare_change`
and selected `inspect` calls for treatment; semantic/advice workflows are a separate condition,
so their cost cannot silently enter a nominally structural comparison. Pin the **tool runtime**
to the original V2 installation, independently of the V2 candidate being edited. Candidate
tests must import the candidate source, never the installed tool by accident.

Every stage gets a **fresh subagent with `fork_turns="none"`** and the same model, effort,
tools, deadline policy and opportunity budget. Root/coordinator may know the arm; implementers
must not inherit this conversation or the other arm's patch. A fresh stage-B agent receives
only its arm's resulting checkout, current request and earlier requirements. Retain failed
attempts and actual calls; no unpaired rescue turn. Keep experimental patches out of `main`.

Score independent behavior first, then blinded responsibility/contracts/simplicity and actual
later-change effort. Record source/context consultation separately from implementation of
advice. Source familiarity and self-hosting limit interpretation. Use an independently authored
external task chain after the protocol is frozen; do not label this prepared task pack held out.

## 3. Controlled opportunity discovery with the V2 runtime

The existing automated runner now supports both the already-cohesive `workshop` seed and
`workshop-coupled`. The latter distributes single-seat assumptions across booking, payment,
notification and reporting; it is still correct for the original public behavior. Neither
worker brief prescribes components, patterns or a target layout. The same four-stage chain
adds group bookings, provider replies, cancellation/refund and shared configuration changes.
The independent oracle carries actual opaque state across stages.

These are constructed products, not natural V2 maintenance tasks. Keep that evidence distinct
from experiments 1 and 2. Synthetic calibration replaces stage implementations with known
fixtures; it tests automation and evaluator behavior, never model usefulness.

Predeclare all three comparisons rather than choosing the favorable one afterward:

| Comparison | Question |
| --- | --- |
| Cohesive seed: ordinary coding vs AnaxiGraph | Does advice add unnecessary work where a small coherent design already suffices? |
| Coupled seed: ordinary coding vs AnaxiGraph | Does advice help discover a useful improvement that pays off in later tasks? |
| Coupled seed: ordinary source reviewers vs AnaxiGraph | Is an improvement specific to structured advice, rather than extra reasoning? |

Create a frozen plan without launching models:

```bash
uv run python -m experiments.architecture_quality.campaign \
  --state-root /home/hcekne/operations/anaxigraph-usefulness-campaign \
  --output /home/hcekne/operations/anaxigraph-usefulness-campaign/plan.json
```

The plan pins maintained input hashes, runtime entrypoint/source, random seed, model/effort,
comparisons, failure/stop rules and runnable argument arrays. Run commands from their recorded
`cwd`. Generated commands carry `--campaign-plan`: source/runtime or configuration drift is
rejected before writing trial state or launching models. Create a new plan after a change.
Preparation never overwrites a previous plan. Qualification commands
are synthetic and make no model calls. Preflight, pilot and comparison commands are live.

Start with the plan's real delivery preflight and **one coupled pair**, then inspect frozen
artifacts before committing to the complete campaign. The default campaign has three repeats
per comparison: **72 coding turns**, plus model-backed analysis, preflights and any active
source reviews. Actual analysis call counts, tokens and monetary cost are unknown beforehand.
The one-pair pilot is separate qualification, not one of the later campaign repeats.

The setup's real delivery preflight passed on 29 October: one fresh Codex turn successfully
called `scan`, `prepare_change` and `inspect`, with independent server ledger events. It took
18.05 seconds; reported usage and the proof hash are in the evidence record. This verifies
tool delivery, not a coding outcome. No new live coding comparison was launched during setup.

A live pilot command, after synthetic checks and preflight:

```bash
uv run python -m experiments.architecture_quality.run \
  --live --track discovery --scenarios workshop-coupled --replicates 1 \
  --control-mode normal --runtime "$PWD/.venv/bin/anaxigraph" \
  --runtime-source-root "$PWD/src" \
  --state /home/hcekne/operations/anaxigraph-usefulness-pilot
```

No result from a delivery failure estimates the effect of successfully delivered advice.
Retain it as operational evidence; do not silently replace the failed arm. Do not interpret
eight dependent task outcomes from one pair as eight independent observations.

## Anonymous subagent review

After a completed trial, prepare a different sheet directory for each reviewer:

```bash
uv run python -m experiments.architecture_quality.review prepare \
  --blind-root /path/to/trial/blind-review --output /path/to/grader-a
```

Launch each reviewer with **`fork_turns="none"`**. Give it only the anonymous bundle directory,
copied rubric, sheet and schema, with this instruction:

> Review every candidate using the supplied rubric. Complete sheet.json with your reviewer
> identity, model/effort, six separate 0–3 judgments, exact source/test quotations and reasons,
> uncertainty, and any suspected disclosure of assignment. Keeping a cohesive design is valid.
> Do not open the trial's private files or infer the arm from names. Do not sum the scores.
> Record behavioral failures separately. Do not edit candidate code or execute arbitrary tests
> that would rewrite its frozen bundle. Give no effectiveness conclusion before assignments open.

Time and token totals are withheld from these sheets. Contract prose can still reveal process;
record suspected unblinding with evidence. The tool verifies artifact hashes and ordering, not
that a reviewer obeyed filesystem restrictions or supplied a correct semantic judgment.

Freeze each completed sheet before joining assignments and measured resources:

```bash
uv run python -m experiments.architecture_quality.review freeze \
  --blind-root /path/to/trial/blind-review --sheet /path/to/grader-a/sheet.json \
  --output /path/to/grader-a.frozen.json
uv run python -m experiments.architecture_quality.review join \
  --state /path/to/trial \
  --frozen /path/to/grader-a.frozen.json /path/to/grader-b.frozen.json \
  --output /path/to/joined.json
```

Use the same process for reviewer B. Final reporting keeps correctness, six ordinal dimensions,
later-change effort, consultation/adoption, analysis/coding usage and unknown costs separate.
Report disagreements and no preference. Model review is not human review, three repeats are an
exploratory pilot rather than a power calculation, and an external project is needed before
claiming transfer. Let these outcomes determine which capability earns the next development work.
