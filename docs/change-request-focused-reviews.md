# Focused, opinionated architectural advice — 27 October 2026

## Purpose and decision

Help people and agents write software that stays understandable and straightforward to change.
The default advice should favor independently developed and verified components behind small,
stable public contracts. Internal changes should remain local when those contracts are preserved.
This applies to a package, a module or a collection of functions; classes and separate services
are optional implementation choices.

The maintainer's examples are rooms in a house and a microwave: work inside one component should
not require understanding every other component. In FlowThink, the extracted workspace engine and
payments library illustrate this direction. Those examples motivate the principle, not a required
package layout for every repository. Contracts include failure behavior and relevant side effects,
not just method names. Targeted checks supplement the repository's required CI rather than proving
the rest of the system cannot break.

Replace the default exhaustive prose matrix with cheap selection followed by focused judgment.
Retain the 133-card catalogue as a reference and an explicit exhaustive-review option. This updates
the earlier instruction to write detailed pros and cons for every module/card pair.

## Observed Snow Rankings baseline

Read from the preserved `snow-rankings-v2` record at commit `af50630`:

- Twelve current pattern reviews contain **1,596 evaluations**: 1,254 score zero, 262 are unknown,
  63 score one and 17 score two. None scores three. Thus 1,516/1,596 have no positive opportunity;
  80 contain a positive opportunity.
- These review results occupy **1,016,382 bytes** when encoded as minified JSON. The complete
  portable record occupies **1,257,081 bytes**. Encoding choices affect byte comparisons.
- Five modules are tests and one is an inert package marker. They received the same 133-card
  written review as production modules. Test functionality remains useful in the graph and map.
- The recorded initial task-four mapping took 375.1 seconds for eleven annotation batches; one
  module already had a current annotation. Pattern review took 1,472.7 seconds for twelve batches.
  These are recorded workflow wall times, not a cold-scan benchmark. Both stages used Sol/xhigh.
- The old workflow files have no individual batch wall durations. Provider receipts retain model
  call durations and token usage. Completed provider calls can still produce rejected analyses;
  retry history and acceptance must remain distinct from transport completion.

The main waste is generating and subsequently reading detailed rejection essays. Input size,
reasoning effort, queueing, concurrent capacity and retries also affect latency; output length alone
does not explain every delay. Preserve the original experiment and analyses as the reference.

## Architecture charter

Make these ten principles explicit in the shared analysis and change-guidance APIs:

1. Give each responsibility an identifiable owner and reuse that owner before duplicating it.
2. Expose a small, deliberate public surface.
3. Preserve caller-visible behavioral contracts while changing implementation details.
4. Keep dependency direction understandable and expose cycles or declared-boundary violations.
5. Keep consumers out of private implementation details.
6. Contain external I/O and side effects behind explicit boundaries.
7. Make components independently testable; identify local checks and contract/consumer checks.
8. Keep ordinary changes local and make their likely impact visible.
9. Choose the simplest useful design; introduce abstractions for demonstrated independent variation.
10. Make failure behavior, uncertainty and resource obligations observable.

Measure public declarations, literal exports, private cross-module references, consumer/dependency
relationships, cycles and observed test links mechanically. Distinguish declared public surfaces
from naming conventions and unresolved dynamic exports. Do not infer required dependency direction
from folder names, equate fan-in with bad design, declare unreferenced code dead, or claim observed
test links establish behavioral coverage.

Use narrow model tasks to judge the remaining opportunities. The cheap scout has one responsibility:
select plausible catalogue IDs. The detailed reviewer has one small shortlist to assess against the
charter, evidence and simpler alternatives. This is the first focused implementation; independent
principle-specific scouts can be evaluated later without creating ten compulsory model calls now.

## Workflow changes

**Mapping:** structural indexing remains model-free. New annotation defaults use `gpt-6-luna` at
low reasoning effort, one independent module per task and a default pool of 32 workers, within the
existing configurable limit. Aim for one overview sentence, a short paragraph and one sentence per
function. Keep essential capabilities/contracts, explicit uncertainty and evidence. Try the cheap
worker first and escalate a genuine failure. Preserve explicit saved model selections and fallbacks.

**Pattern screening:** default scope contains production modules, excluding tests and inert markers.
Keep active package facades eligible. Explicit scopes may include other modules. Structural facts
and the charter help a cheap model consider the full catalogue and return only zero to ten selected
IDs per module. Three to ten is a useful expected range, not a quota. No selection is valid. Avoid
unsound hard filters: Python functions or modules can implement an adapter, strategy or singleton
even without classes. Record considered/selected/unevaluated coverage without per-rejection prose.

**Detailed review:** use the stronger configured model only for selected IDs. Require concrete
incremental benefits, costs, evidence and a simpler alternative. A pattern already implemented well
does not earn an improvement score simply because it fits. Keep unknown distinct from zero. Empty
shortlists require no strong-model call. Default agent responses summarize actionable opportunities;
explicit inspection can expand saved detail.

**Incremental work:** reuse annotations and scoped reviews when their actual input fingerprints and
requested model pipeline remain compatible. New commits, unrelated documentation and unrelated
implementation edits should not trigger a whole-repository review. Source/public-contract/topology
changes invalidate affected evidence. Opaque configuration, frontend resources and cited obligations
remain guarded. A cached result is not a new model call, and unchanged signatures do not prove that
behavior was preserved.

**Fresh Eyes:** remains a separate, on-demand or explicitly chosen milestone review, outside the
default mapping-plus-Patterns path. Preserve source blindness, behavior-only requirements, multiple
alternatives and separate final reconciliation. Do not rewrite frozen experiment protocols or remove
this capability as a speed shortcut.

**Measurements:** save per-batch start/end timestamps, measured wall time across attempts, model
call time and counts, cached/reused work and token usage where supplied. Keep ordered review passes,
immediate saving, interruption recovery, bounded failure history and provider/result distinctions.
Show screening and detailed review as distinct phases, plus completed/total, workers and elapsed
time. Existing explicit workflows and saved results remain readable.

## Acceptance and live qualification

- New defaults are cheap and bounded; explicit project configuration is preserved.
- All catalogue cards are available to screening. Only selected cards receive detailed prose.
- Test/inert exclusions, screened coverage and unevaluated pairs are visible rather than hidden.
- An interrupted run preserves accepted screens and detailed batches; only unfinished work retries.
- Repeated unchanged runs use no new model calls. A local leaf edit refreshes its evidence without
  invalidating unrelated scopes. Changed public contracts/resources still invalidate relevant work.
- The charter produces evidence-backed structural findings and useful component verification advice.
- Existing explicit exhaustive reviews, portable provenance, saved analyses and Fresh Eyes survive.
- Run real mapping and Patterns on an isolated copy of the same Snow Rankings source, recording
  profile, revision, actual wall/model durations, tokens, output bytes, selections and failures.
  Then make a small controlled edit and repeat. Preserve the original app and record.

Targets for this small repository are approximately one minute for mapping, under five minutes for
mapping plus Patterns, and under one minute after a small edit. These are targets to measure, not
deadlines that kill workers or performance claims. Report misses and recommendation-quality limits.
Do not claim a timing or output improvement establishes better architectural decisions.

OpenAI's [Luna model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna) lists
low reasoning effort; [Codex model guidance](https://learn.chatgpt.com/docs/models) describes Luna
for focused, repeatable work. Account access and actual performance are checked by the live run.
