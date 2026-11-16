# Evals on a real repository

Small scripts that measure AnaxiGraph on a large existing repository instead of on a benchmark
application. None calls a model, and none is tied to a particular repository: each takes a
throwaway clone, because pull requests are replayed by checking the clone out at their parent
commits. Pull requests are recognised as GitHub merge commits from `fix/`, `feat/` or
`feature/` branches.

| Question | Script | What it reports |
| --- | --- | --- |
| Does the map link the code, and how fast does it answer? | `scripts/map_health.py CHECKOUT` | Internal imports linked, files read, scan, rescan, new-process, new-worktree and brief times |
| Do suggestions point at the files a pull request really changed? | `replay_navigation.py CLONE OUT`, then `compare_navigation.py OUT` | Top-1, top-3 and top-10 hits against plain keyword search (BM25), and candidate ranking signals chosen on half the pull requests and judged on the other half |
| Which tests would be picked for each pull request? | `replay_tests.py CLONE --coverage ... --junit ...` | Share of suite time picked, how often the whole suite is selected and why, and whether any test that executed a changed file was left out |

`replay_tests.py` needs one recorded run of the suite at the clone's commit, made with one
coverage context per test (for pytest: `--cov --cov-context=test --junitxml=...`). The
recording is the only step that executes project code, and the project runs it itself.

A ranking signal belongs in the product only when it beats keyword search here, and a
selection rule only when the replay shows no executing test left out. Results from the first
run are in [the real-repository plan](../../docs/real-repository-plan.md).
