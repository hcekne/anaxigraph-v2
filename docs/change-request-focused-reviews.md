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
shortlists require no strong-model call. Keep one current default shortlisted-detail result per module
as selections change; retain exact selected IDs in validation and fingerprints. Explicit selected and
exhaustive shards and explicitly saved analyses preserve their retention. Metadata-only promotion
reuses compatible results without a model call; other unknown legacy shards are preserved.
Default agent responses summarize actionable opportunities;
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

## Completed qualification — 27 October 2026

The isolated Snow Rankings run used package **1.0.0a1** at `7ebc133`, the same source revision
`af50630`, 32 workers, Luna/low for mapping and screening, and Sol/medium for selected detail with
Sol/xhigh available as fallback. Its worker timeout was explicitly unlimited. Old records were
withheld so compatible saved analyses could not make the first run appear cold. Exact revisions,
wheel hashes, aggregate receipts and limitations are in
[the qualification record](evidence/focused-advice-qualification.json).

| Measurement | Preserved reference | Focused run |
| --- | ---: | ---: |
| Mapping wall time | 375.1 s; one annotation already current | 21.8 s; 12 annotation calls |
| Patterns wall time | 1,472.7 s | 61.3 s |
| Mapping plus Patterns | 1,847.8 s | 83.1 s |
| Detailed module/card ratings | 1,596 across 12 modules | 43 across six production modules |
| Minified pattern-result JSON | 1,016,382 bytes | 30,504 detail + 3,215 screening = 33,719 bytes |
| Portable record | 1,257,081 bytes, including Fresh Eyes | 103,091 bytes, map and focused Patterns |

These are different workloads and model profiles, not a controlled speed or quality comparison.
The new screen exposed all 133 cards for each of six eligible modules: **798 pairs exposed,
43 selected and rated**. Exposure does not establish individual reasoning, a rating or irrelevance
for the other pairs. Five test modules and one inert marker were excluded from default Patterns;
all twelve modules retained current mapping annotations. Fresh Eyes remains available but was not
rerun in this timing sample, so the portable sizes do not compare identical analysis contents.

An unchanged repeat finished in **0.7 seconds with zero model calls**. A behavior-equivalent body edit that
preserved the public surface took **63.5 seconds and three calls**, all confined to the edited
module: annotation, screening and detail. This missed the 60-second warm target by 3.5 seconds.
Across cold, unchanged and edited runs, receipts recorded 27 actual calls and 27 accepted analyses;
reuse events were separate. Dollar cost was unavailable, not zero.

Three small synthetic recognition cases then exercised the same frozen package. All nine compatible
annotations and five screen batches were reused; twelve new calls completed without retry or
fallback. The positive cases retained a shared provider contract (Adapter 3, provider abstraction 2)
and atomic replacement through one transaction (Unit of Work 3), including costs and simpler local
alternatives. The negative case avoided another caller-facing provider boundary but still suggested
optional private adapters at score 1; an already simple formatting function also consumed three
strong reviews that all returned zero. The negative result is therefore partial. Selected-detail
JSON fell from 44,065 to 28,847 bytes and output tokens from 12,686 to 8,675 across eight strong
calls in each run, reviewing 37 versus 38 pairs. Reuse, stochastic output and changing shortlists
prevent a causal claim; these cases do not prove general recommendation quality.

The same-source disagreement audit compared 798 old production ratings with the 43 selected pairs.
Of 49 old positive ratings, 35 were not selected: 32 scored one and three scored two. These old
judgments are not ground truth or a recall benchmark. Of the three higher-scored omissions, a pure
result-projection idea survived under Extract Function; the proposed separate contract-test suite
had overlapping existing checks. **Per-location single-flight remained a plausible omission**:
the batch lock can block unrelated forecast work while provider slots remain free. A bounded
in-flight registry has coordination costs, and concurrent demand has not been demonstrated.
The audit does not justify calling every omitted opportunity unnecessary.
Private-boundary facts cover resolved cross-module function, class and method definitions; private
globals and instance attributes remain incompletely resolved, so zero findings do not establish
complete coverage. This qualification did not extend the parser's coverage.

GitHub CI passed for the [qualified `7ebc133` package](https://github.com/hcekne/anaxigraph-v2/actions/runs/40503446255)
and for [`94156a6`](https://github.com/hcekne/anaxigraph-v2/actions/runs/40506824918), which subsequently
shared the source-independent charter with Fresh Eyes. That later prompt change has no new live
Fresh Eyes quality result. Both runs exercised Python 3.11/3.12 and the built wheel through uvx.

A real Sol/xhigh agent then made five successful MCP calls against `94156a6` in 24.342 seconds,
including change guidance and workflow configuration/status; it confirmed the ten charter principles
without changing source or starting analyses. Separate browser checks used synthetic providers,
verified desktop/mobile workflow controls and access to all thirteen paginated batch-timing rows,
and reported no browser errors. Those UI checks made no paid model calls and are not model benchmarks.

Local deployment of `94156a6` was measured separately: both connected AnaxiGraph services were
active and their installed source matched the commit. They expose the focused defaults while the
existing Snow trial retains its unlimited timeout and Sol/xhigh Fresh Eyes choice. Structural scans
refreshed boundary metadata without starting model jobs; the original Snow checkout stayed clean,
its portable record stayed unchanged and its preview remained active. This is a source/CI/local
deployment update; **no PyPI release was published**.

A separate local Git clone of the isolated edited fixture, at a different path with a new database,
imported twelve current annotations, six screens and six detailed reviews: 43 ratings from 798
catalogue-exposure pairs. Mapping completed in 0.7 seconds with no pending batches; Patterns took
2.1 seconds with all twelve review batches reused and zero provider invocations. Selected IDs,
input fingerprints, model pipelines and source provenance survived. Three unchanged scan/import/
export cycles kept the **103,091-byte record byte-identical**, with a stable 475,136-byte database
and six generated files (612,379 bytes total). This used a local fixture commit without a remote
push; it does not establish a GitHub round trip. Fresh Eyes was absent and was not tested here.
