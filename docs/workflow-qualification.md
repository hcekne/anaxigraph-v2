# Workflow and portability qualification

25 October 2026. This extends the first replacement-core milestone with the capabilities selected
in [the change request](change-request-workflows.md): internal model execution, resumable workflows,
portable understanding, stable change baselines and incremental updates.

## Implemented behavior

- Preparation pins a retained baseline. Repeated scans cannot erase an introduced or worsened
  finding from the change comparison. Explicit baseline selection starts the next change.
- Long-running services reuse verified file captures, parser observations and unchanged references.
  Annotation writes update one search document; model-request writes do not invalidate the graph.
- Dependency freshness ignores comments and formatting, while retaining conservative invalidation
  for meaningful syntax changes. Own-source and annotation-contract identity remain separate checks.
- Whole-repository work is partitioned. Per-job input/result budgets replace the 256 KB whole-analysis
  constraint. Pattern reconciliation verifies every declared module/card pair. Fresh Eyes uses blind
  subsystem briefs and recursive product design, retaining three alternatives per level.
- Provider/model choices, stronger fallbacks and ordered review passes are configurable. Independent
  batches run in parallel. Accepted results persist immediately; interruption retries unfinished work.
- The dashboard retains its table/graph and adds full-workflow controls, model settings, progress,
  reconciliation, explicit baseline controls and portable-record actions.
- A single Git-carried `anaxigraph/record.json` preserves annotations, reviews and provenance.
  Imports validate source, dependency, annotation-contract and analysis-input compatibility. Product
  child identities are reconstructed in dependency order. Incompatible understanding remains stale.
- Local review payloads are compressed. Retention keeps canonical current scope results rather than
  silently discarding all but twenty batches. Schema migration keeps one recoverable backup.
- Portable publication is atomic and directory-anchored. Size failures retain the last valid file and
  remove temporary output. Concurrent knowledge changes invalidate publication caches.

## Measured structural performance

An isolated checkout of legacy commit `3c65b71` supplied 639 Python modules and 867 eligible files.
Its old-format portable folder was moved aside only in the temporary checkout. This measures V2
structural mapping and new-format publication; it does not demonstrate legacy-record migration.

| Operation | Elapsed | Source bytes read | Python modules parsed |
| --- | ---: | ---: | ---: |
| Cold scan and publication | 12.3136 s | 8,012,026 | 639 |
| Unchanged scan | 0.5244 s | 0 | 0 |
| Second unchanged scan | 0.5525 s | 0 | 0 |
| One small comment edit | 5.5102 s | 392 | 1 |

The first database was 4,771,840 bytes and the structural-only portable record 92,490 bytes.
Unchanged cycles retained those sizes. After the edit, two graph snapshots occupied 8,155,136 bytes;
the record remained 92,490 bytes. The changed scan still publishes a canonical graph. These are
shared-host measurements without model work; they are not full semantic-mapping timings.

## Executed checks

The integrated suite passed 175 tests after the final application refactor. Scale fixtures include
640-module recursive blind design and 532 catalogue evaluations exceeding the former 256 KB limit.
They establish coverage and persistence mechanics using synthetic results, not recommendation quality.

Real installed provider calls passed: a Codex transport probe and a Claude Haiku probe (4.319 seconds).
A real one-module mapping used Luna with a configured stronger fallback, completed in 6.4 seconds,
and saved one current annotation plus a 1,646-byte portable record. The stronger fallback was not
needed. This is a tiny fixture, not a throughput or architectural-quality measurement.

An actual Chromium journey exercised HTTP, the Application, SQLite and workflow persistence with
explicitly synthetic model responses. It verified full mapping, 266 pattern pairs, hierarchical
Fresh Eyes, model/review settings, active-worker progress, module sorting/graph inspection, baseline
controls and record import/export. Desktop 1440 px and mobile 390 px had zero console/page errors
and no horizontal overflow. The temporary server and fixtures were removed afterward.

The final local wheel is 116,043 bytes (SHA-256
`ee33166b753e8e63f5ff03ff8335c41f317704e03f86ba14e7d63a72cabf2721`). Its runtime
files match source, and the superseded monolithic application file is absent. An isolated `uvx`
journey from outside the repository scanned a fixture, accepted a synthetic annotation, exported a
1,097-byte record, reused it from another absolute path and loaded all 133 pattern cards. An actual
MCP stdio handshake exposed 17 tools and prepared/validated against the same pinned baseline.

## Limits that remain

The controlled with/without agent comparison has not happened. The maintainer's development trial
and branch comparisons should measure functional outcomes and the subsequent task, alongside time
and model cost. Passing schema, coverage and transport checks does not establish better architecture.

Python remains the supported parsed language. Dynamic references can remain unresolved. Dependency
invalidation uses normalized syntax including bodies, rather than a complete behavioral contract
model. Fresh CLI processes have cold caches. Blind requirement compression checks attribution and
coverage, but cannot prove semantic completeness or architectural novelty.

Legacy-format records are preserved and rejected explicitly; legacy paid-analysis migration is
separate from this V2-to-V2 portability work. This change does not publish PyPI or replace a running
legacy service.
