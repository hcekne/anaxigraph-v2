# Audit fixes and the Snow Rankings pilot

AnaxiGraph helps humans and coding agents understand their code, choose sound designs and make
subsequent changes easier. A map supplies evidence; concrete ownership, reuse, interface and testing
guidance connects it to the coding task. Patterns and alternate architectures remain proposals with
benefits and costs, rather than commands to add abstractions.

## Review findings and changes

The independent review of `e686a7f` verified the first fixes but identified regressions and missing
trial evidence. This follow-up addresses them:

| Finding | Current behavior |
| --- | --- |
| A baseline remained pinned across unrelated tasks and commits | A changed normalized task or Git HEAD starts a fresh baseline. Explicit continuation preserves the existing task. |
| Fresh Eyes rejected ordinary English matching symbol names | The leak check rejects code-shaped identities and source paths, while allowing ordinary prose. |
| No useful record of the development trial | A bounded local ledger records task/revision/baseline, supplied versus explicitly inspected context, validation reports and actual provider usage. Unknown costs remain unknown. |
| Dependency bodies caused paid annotation fan-out | Public signatures, docstrings and exports govern caller freshness. Body changes are flagged separately as behavioral uncertainty; they do not prove preserved behavior. |
| Type errors and unchecked code growth | CI runs mypy, a physical production-line budget and scope-aware complexity regression checks. Existing complexity debt is explicit; limits are not automatically refreshed. |
| Git status was a constant | Publication compares the record with local HEAD and the configured upstream tracking reference, exposing unknowns and its local-ref proof. No fetch or push is implied. |
| Ordinary scans unexpectedly wrote a portable record | Scans update local understanding and may import compatible records. Publication requires explicit export, `scan --export`, or a full workflow. |
| A real source package named anaxigraph disappeared | Only recognized generated artifacts are excluded. Actual Python source survives publication into the same folder. |
| Unchanged exports changed after record-only commits or cloning | Canonical source/analysis provenance is retained when inputs and results are unchanged. |

Change preparation now includes concise implementation advice and current, owner-relevant reviewed
proposals. Relevant review metadata is selected in SQLite before large results are decompressed.
Suggestions include evidence, uncertainty, tradeoffs and simpler options. The default brief remains
bounded; full analyses are explicit expansions.

## A separate controlled pilot

The full [Snow Rankings specification](snow-rankings-specification.md) and
[experiment protocol](snow-rankings-experiment.md) define the product and comparison. Two private
repositories started from the identical commit `901251e`: `hcekne/snow-rankings` and
`hcekne/snow-rankings-control`. Both coding agents request GPT-6.1-Sol with xhigh reasoning and the
same four-task sequence and wall-clock allocation. Only the treatment receives AnaxiGraph.

The treatment runtime is frozen at `dcc1a68`. Later tool improvements are not injected into its
ongoing run. Ordinary coding, provider calls, acceptance checks, commits and token usage are kept
separate. A single matched pair is exploratory evidence and cannot establish a causal winner.

The first shared checker incorrectly required integer powder days although the specification asks
for expected powder days. Both exact first commits pass when that unpublished restriction is removed.
Original 3/4 results and paired corrected 4/4 diagnostics are preserved. Later assessment revisions
must apply equally, retain original results and disclose that both agents received the original
failure summary. Neither a checker repair nor passing API tests establishes architectural superiority.
