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
| Which tests would be picked for each pull request? | `replay_tests.py CLONE --coverage ... --junit ... --reads ...` | Share of suite time picked, how often the whole suite is selected and why, and whether any test that executed or read a changed file was left out |
| How much of the suite does a change need, and what would lower it? | `reach_report.py CLONE --suite NAME` | Test reach: the share of suite time the typical past change needs, where it comes from, the parts ranked, the files that cost most, and what-ifs, each replayed |
| Where are the walls between the parts, and does a change add a reach-around? | `walls.py CHECKOUT --scope DIR`, with `--base REVISION` for what a change adds | Per part the names other parts use of it (its doors), uses of private names across parts, and the new ones since a revision; reads a `tach.toml` for declared parts |
| Would the picked tests have caught a fault? | `plant_faults.py CLONE FAULTS.json --run '...' --out DIR` | For each planted fault: the test files that fail in a whole-suite run, and those among them the selection would have left out |

`replay_tests.py` needs one recorded run of the suite at the clone's commit, made with one
coverage context per test and the reads recorder (for pytest: `-p anaxigraph_reads --cov
--cov-context=test --junitxml=...`, see the usage guide). `plant_faults.py` needs the record
saved in the clone with `anaxigraph record-tests`, and runs the project's own suite command
once per fault and once without any. These runs are the only steps that execute project code.
`reach_report.py` reads the record saved in the clone and replays merged pull requests, or
every commit on the main line with `--history commits`; it is the scoreboard of
[the roadmap](../../docs/roadmap.md). `walls.py` needs neither a record nor history: it reads
the map of the checkout, and of an earlier revision when asked what a change adds.

A ranking signal belongs in the product only when it beats keyword search here. A selection
rule belongs there only when the replay leaves out no test that executed or read a changed
file, and the planted faults show no failing test file left out. Results are in
[the real-repository plan](../../docs/real-repository-plan.md).
