# Historical commit replay

A cheap, repeatable check of AnaxiGraph's core promise: given a task, does it point a coding
agent at the code that actually has to change, and at the consumers and tests that change with it?

For each selected commit, the replay checks out the commit's parent in a scratch clone, treats the
commit message as the task, and compares AnaxiGraph's suggestions with the files the commit really
changed. Three baselines need no AnaxiGraph and no model:

- **grep owners:** rank production files by distinct task words in their text and path.
- **grep importers/tests:** files importing the changed module by name (two levels), and test files
  mentioning it.
- **history:** files that changed together with the module in earlier commits (logical coupling).

```bash
# Free: structural map, grep and history baselines.
uv run python experiments/owner_replay/replay.py --source /path/to/repo \
    --workdir /tmp/replay --commits 20 --mode structural

# Paid: also maps each parent with the configured workflow models before asking.
uv run python experiments/owner_replay/replay.py --source /path/to/repo \
    --workdir /tmp/replay --commits 20 --mode mapped [--config workflow.json]
```

The clone walks forward oldest-first, so mapped mode refreshes annotations incrementally for each
parent, as a maintained record would. It never sees code written after the replayed parent.
Results append to `<workdir>/results-<mode>.jsonl` (rerun to resume) with a summary beside them.
Do not commit raw results from private repositories: they contain commit messages and paths.
Use a fresh workdir for each implementation or settings change. The manifest freezes runtime,
harness, selected full commit IDs, task text and configuration; unverified warm databases are
rejected. This prevents future/stale annotation leakage from an earlier replay. Duplicate or
abbreviated result identities are rejected; interrupted runs resume without appending duplicates.

The free structural mode now exercises source/symbol retrieval without semantic annotations.
`symbols` removes source/annotations; `grep` is the independent source baseline; `fused` combines
grep with the delivered owner ranking. Mapped mode adds saved semantic understanding. Delivered
and unpacked results stay separate. No embedding/vector service is required.

Version 2 uses `--candidate-budget 6` as the same maximum for graph, grep, history and combined
consumer/test lists. Graph and grep rank direct references before second-level references;
history ranks support count; combined uses reciprocal rank fusion. Precision, recall and actual
list sizes are reported. Eligible list-size averages use the same commits as their recall
denominators. The oracle is conditional on a known changed file; it does not measure history's
ability to find owners from a task, complete behavioral impact, or permission to skip tests.
Use `--tasks private-tasks.json` for full-commit-ID to pre-existing task descriptions instead of
commit-message proxies. Reserve unseen commits/repositories before tuning on these cases.

## Metrics

| Metric | Question |
| --- | --- |
| `*_owner_rank`, hit@1/hit@3 | Is a changed production file among the suggested owners? |
| `*_uncapped` | The same brief before the default 3.4 KB size cap trims its lists. |
| `task_*_affected_recall` / `_size` | From the suggested owners, does the affected set reach the other changed files, and how large is it? |
| `oracle_consumer_recall_*` | Given the true primary file, which method finds the other changed production files? |
| `oracle_test_recall_*` | Given the true primary file, which method finds the changed tests? |

Recall counts only files that existed in the parent. Sizes matter: flagging every module would
reach perfect recall and be useless.
Commits creating only new production files have no existing-file owner label; they remain in
the replay but are explicitly excluded from owner accuracy denominators.

## Original result — 28 October 2026 (harness version 1)

Target: a private, actively developed 19k-line Python service (104 modules), 20 most recent
commits changing 1–8 production Python files. AnaxiGraph runtime `20c16a0`, default mapping
models (Luna/low with Sol/medium fallback). Mapping walked 20 parents in about 17 minutes and 306
model calls after an earlier warm-up run; the first two parents had partial annotations (63 and
88 of 104 modules) because some batches failed validation or hit a local `OperationalError`.

**Owner finding**

| Method | hit@1 | hit@3 |
| --- | ---: | ---: |
| Mapped, brief as delivered | 3/20 | 3/20 |
| Mapped, before size cap | 3/20 | 9/20 |
| Structural only | 4/20 | 8/20 |
| grep | 7/20 | 11/20 |

With annotations, every delivered brief exceeded the 3.4 KB cap and was trimmed to a single owner.
Before trimming, mapping added one top-3 hit over the structural map and still trailed grep.

**Co-changed files and tests, given the true primary file**

| Method | Other production files found | List size | Changed tests found | List size |
| --- | ---: | ---: | ---: | ---: |
| AnaxiGraph dependency map | 19% | 5.3 | 79% | 7.6 |
| grep importers / test mentions | 19% | 5.4 | 74% | 4.5 |
| Git co-change history | **72%** | 6.2 | 73% | 6.9 |

Ten commits had more than one changed production file; eleven changed existing tests. The map
matched grep for consumers. Files that change together here mostly do not import one another, so
history found almost four times as many with a comparable list.
Those list-size averages covered all twenty cases, while recall covered only ten consumer and
eleven test-changing cases. Version 1 also gave history a minimum of three candidates; its budgets
were approximately comparable, not identical. Its resume-ID bug did not affect these twenty unique
rows. These historical figures must not be mixed with version 2's equal-budget measurements.

## Limits

One repository, twenty commits and commit messages written as records rather than task requests.
Commit messages can be vague or name evidence rather than code. These numbers are a smoke test of
the owner-finding claim, not a general accuracy estimate. Repeat on other repositories and with
real task statements before generalizing. Tool effects on coding outcomes need separate trials.

## Follow-up — repaired delivery and free retrieval

The [aggregate follow-up](../../docs/evidence/owner-replay-followup.json) reruns the exact same twenty
development cases without model calls. Source/symbol retrieval returns three owners throughout:
hit@3 **10/20**, versus grep **11/20**, symbols alone **6/20**, and rank fusion **11/20**.
Delivered and unpacked owner lists match. This repairs delivery and improves the original free
structural result (8/20), but does not demonstrate superiority over grep. Semantic mapping was
not rerun, and the previous 306-call mapping cost is not a new run's cost.

At a common maximum of six suggestions, history reaches **61.5%** conditional production recall,
versus **13.5%** for graph/grep and **43.5%** for fusion. Eligible average sizes are 4.8, 3.8,
3.8 and 4.8 respectively; precision is reported alongside recall. Fusion should not replace the
separate history signal on these results. No held-out or cross-repository advantage is claimed.

A subsequent single scoring correction removes hard priority for ordinary English words that
also happen to be function names, while retaining explicit path and underscore-identifier
bonuses. On the development cases it reaches **7/20** first owners and **14/20** top-three
owners, versus grep's 7/20 and 11/20; paired top-three gains/losses are 5/1. The policy was then
frozen before testing an independent repository's Python slice. Thirteen of its twenty commits
have existing-file owner labels: old/new/grep top-three scores are all **9/13**; first-owner
scores are 4/13, 4/13 and 7/13. No held-out improvement or architecture benefit is established,
and there was no further policy tuning. Raw private cases stay outside this public repository.

Default brief delivery is measured separately from oracle graph recall. Prioritizing tests named
for the selected owners raises mean delivered test recall from **10.6% to 47.0%** on eleven
eligible development cases (four gains, no losses), with the same 2.55 average list size across
twenty cases. This final measurement uses direct core composition; reconstruction with the old
ordering matches all twenty prior Application results. Relevant tests still fall outside the
bounded list: it never authorizes skipping required project checks.
