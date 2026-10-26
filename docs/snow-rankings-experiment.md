# Snow Rankings controlled development experiment

The experiment tests whether AnaxiGraph helps an agent deliver correct software with clearer
responsibilities and interfaces, and makes a subsequent change easier. Snow Rankings is a useful
product in its own right; both implementations should pursue the same good result.

## Matched starting conditions

Use two separate private repositories, `snow-rankings` and `snow-rankings-control`, created from the
same bare initial Git commit. Separate repositories simplify tool/configuration isolation and keep
each agent's history independent. They can later be compared through neutral checkouts. Branches
would also work, but shared agent configuration and accidental inspection are easier to mix up.

Both agents use **GPT-6.1-Sol with xhigh reasoning**, the same shared brief/seed/API, fixed stack,
task sequence, validation tools and declared coding budgets. Neither agent receives the other arm's
code, reviews, progress or results. The control receives normal instructions to write maintainable
code, not instructions to code loosely or produce a bad baseline.

The treatment receives AnaxiGraph access, preparation/inspection before meaningful edits, validation
afterwards, and regular Patterns/Fresh Eyes review at milestone boundaries. Record which guidance it
actually reads, whether it changes its plan, and the implementation/rejection reason. Fresh Eyes
must remain source-blind; the source-aware coding agent may assess frozen alternatives separately.
The control retains ordinary source search, compiler/linter/tests and its own judgment, but no
AnaxiGraph MCP, index, summaries, catalogue recommendations or treatment history.

The machine-readable preregistration is
[`manifest.json`](../experiments/snow_rankings/manifest.json). Record actual seed and AnaxiGraph
commit IDs before launch. Changes to the protocol after seeing outcomes must be recorded as such.
The first allocation is four equal twenty-minute coding tasks per arm; the launcher records actual
limits. Unknown token/cost limits are null, not fictitious measured caps. Stop and report overrun
rather than silently giving a struggling arm additional iterations. If an external service or host
failure affects one arm, record it and apply the same repair allowance to both.

## Tasks and observation

The three build milestones and one chained maintenance task are identical and sequential in each
arm. Agents can run in parallel across arms. Commit at each boundary and capture the starting/candidate
revision. Evaluate before giving the next task; do not cherry-pick improvements between arms.

Record for every task: task ID/text, requested/actual model and reasoning effort where observable,
starting revision, timestamps, session ID, outcome, candidate revision, check results, review effort,
and token/cost data with source and completeness. Treatment records also include pinned baseline,
snapshot, context actually consulted, analysis calls, recommendations acted on/rejected and reasons.

Coding and AnaxiGraph model work must be reported separately and included together in total cost
and time. Identical coding-model settings alone do not imply equal total resources. If a provider
does not expose monetary cost, retain null. Any price-derived estimate must record the price source,
date and billing basis and remain an estimate. Cached input and retries must not silently disappear.

The neutral stdlib harness at `experiments/snow_rankings/trial.py` stores bounded local evidence
without importing the AnaxiGraph product. It distinguishes context supplied from context consulted,
and partial known sums from complete totals. Export those records with acceptance/review artifacts;
do not commit full source-bearing model transcripts as portable analysis.

## Evaluation

Run the same external HTTP checker against both servers after every milestone. It verifies startup,
catalogue/search/detail, seven-day data shapes, cost arithmetic, sorts/filters, currencies, input
validation and preservation of behavior during the nights/travel-time change. Provider-stub tests
and desktop/mobile inspection cover behavior outside that checker. Record unsupported/broken
checks as failures or unmeasured, not passed.

Use an independent reviewer with anonymized arm labels and no treatment logs first. Review the same
starting/candidate changes using a short fixed rubric:

| Outcome | Evidence |
| --- | --- |
| Correctness | Shared acceptance results, regressions and relevant executable tests. |
| Responsibility | A change has an understandable owner; unrelated concerns are not bundled. |
| Interfaces | Inputs, outputs, errors and obligations are explicit and callers remain insulated. |
| Complexity | Added concepts, conditionals/dependencies and abstractions have an actual benefit. |
| Reuse | Existing capabilities are found before duplicating behavior. |
| Subsequent change | Time to locate/edit/verify, spread of edits, preserved guarantees and task4 success. |
| Human effort | Review/correction time and concrete findings, not a vague architecture score. |

Assign a bounded 0–3 judgement for each qualitative criterion, cite concrete code evidence, and
state uncertainty. Only after that blind review should the reviewer inspect treatment consultations
to explain mechanisms. Dependency cycles, source lines and complexity are descriptive diagnostics,
not automatic definitions of quality.

## Interpretation and limitations

### First pilot invalidation on 26 October 2026

The first pilot (`snow-rankings-2026-10-26`) cannot estimate AnaxiGraph's effect. All 31 terminal
treatment MCP calls failed at the approval boundary; no working map, preparation, inspection or
review result was delivered. The frozen treatment reports the error as “MCP tool call requires
approval, but approval policy is never.” Assignment and server attachment did not establish use.

Both implementations completed four coding tasks. Their original as-run scores remain preserved;
separate version2 adjudication of the exact frozen commits passes 4/4, 8/8, 8/8 and 9/9 checks for
each arm. Those results establish the scoped product behavior, not better architecture or a benefit,
harm or absence of effect from a working AnaxiGraph. The original failed checker summaries also
reached both agents before correction and remain a limitation.

The original ledger retains eight false-positive `context_consulted` rows generated from four failed
inspect calls. The original launch-verification count also counted terminal events as though they
proved delivery. The separate `pilot-invalidation.json` record in the first pilot's state directory
invalidates those interpretations without deleting data and hashes the preserved original artifacts.
The corrected rerun uses separate `snow-rankings-verified-2026-10-26` state; its evidence must not be
merged with this pilot. Tool-effect analysis requires verified successful delivery in that new run.

This is one exploratory matched pair, not proof of a general causal effect. Different stochastic
trajectories, developer interventions, model/provider changes and unequal analysis resources can
explain differences. The setup must expose those differences rather than proclaiming a winner.
Repeat with fresh starts or additional projects before claiming AnaxiGraph improves outcomes.

The initial comparison is particularly useful for finding failure modes: ignored advice, bad
recommendations, excessive tool cost, summaries that miss an existing capability, contract regressions
or improvement only on the second task. Improve the tool from those observations and preregister
another comparison, rather than rewriting the first protocol after seeing its result.
