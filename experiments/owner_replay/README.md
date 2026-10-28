# Historical commit replay

A cheap, repeatable check of AnaxiGraph's core promise: given a task, does it point a coding
agent at the code that actually has to change, and at the consumers and tests that change with it?

For each selected commit, the replay checks out the commit's parent in a scratch clone, treats the
commit message as the task, and compares AnaxiGraph's suggestions with the files the commit really
changed. Three baselines need no AnaxiGraph and no model:

- **grep owners:** rank production files by distinct task words in their text and path.
- **grep importers/tests:** files importing the changed module by name (two levels), and test files
  mentioning it.
- **history:** files that changed together with the module in earlier commits (logical coupling),
  given the same list length as AnaxiGraph's.

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

## First result — 28 October 2026

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

## Limits

One repository, twenty commits and commit messages written as records rather than task requests.
Commit messages can be vague or name evidence rather than code. These numbers are a smoke test of
the owner-finding claim, not a general accuracy estimate. Repeat on other repositories and with
real task statements before generalizing. Tool effects on coding outcomes need separate trials.
