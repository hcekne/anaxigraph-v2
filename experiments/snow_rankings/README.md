# Snow Rankings matched pair experiment

This harness is independent of the installed AnaxiGraph runtime. It holds the common product
brief, external acceptance contract, synthetic seed, ordered tasks, preregistered manifest and
bounded experiment evidence. Product architecture and implementation belong to the two app repos.

Copy `brief.md`, `api-contract.md`, `seed.json` and `tasks/` into the shared initial commit. Both
coding agents receive that same content and use GPT-6.1-Sol with xhigh reasoning. Only the treatment
arm receives AnaxiGraph access/instructions. Do not give either agent the other's code or results.

After each task, run the shared checker from this neutral checkout:

```bash
python3 experiments/snow_rankings/acceptance.py --base-url http://127.0.0.1:PORT --milestone 1 --output result.json
python3 experiments/snow_rankings/trial.py --database trial.sqlite record --arm control --task 01-foundation --kind task_outcome --payload outcome.json
python3 experiments/snow_rankings/trial.py --database trial.sqlite compare --output comparison.json
```

The coordinator can run both arms concurrently after the repositories and frozen treatment runtime
are prepared:

```bash
python3 experiments/snow_rankings/run_pair.py --dry-run
python3 experiments/snow_rankings/run_pair.py
```

Its defaults select `~/repos/snow-rankings` and `~/repos/snow-rankings-control`, four tasks of twenty
minutes each, and the frozen executable at
`~/.local/state/anaxigraph/experiments/snow-rankings-2026-10-26/runtime/bin/anaxigraph`.
Every task gets a fresh ephemeral GPT-6.1-Sol/xhigh context with ignored user configuration and
equal sandbox/tool settings. Only the treatment has an explicitly configured AnaxiGraph MCP server.
Later task files are copied immediately before their turn; the nights change is withheld until task4.
The coordinator runs the shared checker against each app's `run.json` command on assigned localhost
ports, stops only owned processes and commits/pushes available work. A coding response does not count
as product completion if external acceptance fails. A single early provider failure may retry within
the same task deadline; later failures remain recorded. Sandboxing is never silently downgraded.

Progress and sanitized event metadata live outside the repos under the experiment state directory.
Per-arm event logs are capped at4MB; full source, command output and model transcripts are discarded.
Task, session, PID, timestamps, actual reported token usage and nullable cost remain available.

An outcome JSON can contain `starting_revision`, `candidate_revision`, `status`, `elapsed_seconds`
and `notes`. Coding and analysis call events additionally accept model/session identities, tokens,
cost, reported provenance and nulls for unknown values. Context consultations identify what the
agent actually inspected, rather than merely the context returned by a preparation call.

The event database is bounded to5MB/5,000events and each event to16KB. It stores no source or full
model transcripts. Export deliberately before starting a declared next run; capacity failure must
not silently delete experimental evidence. Compare reports partial sums with known/unknown coverage,
not fabricated complete totals. The black-box checker does not grade frontend usability, architecture,
live forecast accuracy or provider resilience; use independent review and provider-stub tests too.

See [the complete product specification](../../docs/snow-rankings-specification.md) and
[the experiment protocol](../../docs/snow-rankings-experiment.md).
