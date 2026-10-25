# Reliable incremental analysis and portable workflows

Requested on 25 October 2026. AnaxiGraph should help people and agents make maintainable changes
through a compact semantic graph, useful reviews and workflows they can run themselves. The
replacement core remains the foundation; feature omissions in its first milestone are now selected
work, rather than reasons to hand the entire process to an external coding agent.

## Problems to correct

The independent review found six defects or missing capabilities:

1. Validation compared with the previous scan. A second scan could hide a regression introduced
   during the same coding task. Preparation must pin a baseline until the caller explicitly changes it.
2. Usefulness has not been demonstrated. Record actual agent usage and support a controlled
   with/without comparison on chained tasks. Do not equate passing validators with improved code.
3. Whole-analysis 256 KB limits, four-module scopes and manual request/submit loops prevent useful
   whole-repository reviews. Keep individual jobs concise, partition work automatically and account
   for the complete declared scope. The repository size must not dictate a single model packet.
4. A dependency's byte hash invalidates callers after comment-only edits. Preserve source identity
   for the edited module while using meaningful public/interface changes for dependent invalidation.
5. Fresh Eyes rejects paths and module IDs but can leak literal function/class identities. Its blind
   input must also reject mapped symbol names, while remaining behavior-only at every hierarchy.
6. Annotation writes rebuild the complete search index, and repeated operations reread every file.
   Update changed documents and source observations incrementally, retaining stale-result protection.

The review also identified strengths to retain: one shared Application; enforced dependency
boundaries; explicit unresolved edges, unknown ratings and contract basis; fingerprint-attributed
check reports; bounded atomic storage; idempotent submissions; concise default responses.

## Product requirements

- Run mapping, catalogue patterns and Fresh Eyes internally with a configurable provider and model
  for each stage, parallel independent batches and configurable stronger fallback models.
- Save each successful batch immediately. Resume interrupted runs and retry failed work without
  paying again for accepted results. Ordered review passes remain ordered within each batch.
- Show completed/total batches, active workers, elapsed time and the named final reconciliation
  phase. Buttons must clearly state that they run the full workflow, or resume remaining work.
- Retain the sortable module table, graph exploration and expandable concise module/function
  understanding. Build on workflow cards rather than requiring users to assemble manual rounds.
- Evaluate every declared module/catalogue-card pair, with explicit ratings or unknowns, current
  fit, proposed benefit, costs, alternatives and evidence. An example pattern must not narrow the
  catalogue. Do not confuse an existing good pattern with a useful change.
- Fresh Eyes receives functional behavior alone. Review subsystems and the product at separate
  levels, keep three alternatives and explicit requirement coverage at each level, and keep later
  source-aware comparison separate from blind design.
- Create `anaxigraph/` at the target repository root. Git carries concise canonical annotations,
  analyses and provenance; local SQLite and deterministic graph/search indexes are rebuilt locally.
  A fresh clone at a different path should reuse compatible analyses without new model calls.
- Apply current source exclusions, validate fingerprints before reuse and retain incompatible saved
  analyses as stale records. Separate mapped locally, record exported and committed/pushed status.
- Keep records and local caches bounded, reject excessive output before publication, preserve the
  last valid record and remove failed temporary output. Unchanged cycles must not create more files.
- Keep uv as the primary execution route. Do not publish a new PyPI release merely for this change.

## Acceptance checks

- A regression remains introduced or worsened across multiple scans after preparation; a caller
  can explicitly select a new baseline and see both comparison identities in the result.
- A comment-only dependency edit leaves callers' interpretations reusable. A meaningful dependency
  interface change invalidates affected understanding. Own-source edits still invalidate that module.
- Updating one annotation changes only its search document. Unchanged files reuse captured
  fingerprints and parser observations; additions, deletions and unscanned changes remain detected.
- A representative 600-module fixture can be partitioned for full pattern and hierarchical Fresh
  Eyes coverage without a whole-repository packet or per-alternative thousands-of-IDs requirement.
- Worker interruption, validation failure and ordered provider passes are exercised end to end.
  Progress survives restart and accepted batches are not repeated on resume.
- A record exported from one checkout imports into another absolute path; changed files are not
  accepted as fresh, saved review provenance survives and repeated unchanged export is byte-stable.
- Real HTTP, CLI, MCP and installed-wheel journeys exercise the shared behavior. CI runs on GitHub.

## How usefulness will be assessed

The maintainer will use AnaxiGraph during ordinary development over the next weeks and may compare
two branches with and without its guidance. Record task, starting revision, prepared baseline,
context consulted, validation outcome and the next task's outcome. Keep indexing/model cost and
elapsed time with those records. A future controlled comparison should hold task/model/resource
budgets comparable and evaluate correctness, unnecessary coupling/duplication, review effort and
success on the subsequent change. This work supplies the workflow and evidence; it does not claim
that this multi-week study has already happened.

## Implementation and verification

The selected changes are implemented in the replacement core. See
[workflow qualification](workflow-qualification.md) for executed checks, measured sizes/timings and
remaining limits. The first milestone's external-only execution and deferred portable-record scope
are superseded. Its original measurements remain historical evidence.
