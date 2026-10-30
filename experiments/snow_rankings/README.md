# Snow Rankings matched pair experiment

This harness is independent of the installed AnaxiGraph runtime. It holds the common product
brief, external acceptance contract, synthetic seed, ordered tasks, preregistered manifest and
bounded experiment evidence. Product architecture and implementation belong to the two app repos.

## Fresh Snow Rankings V2 build

The maintainer requested a new build in `~/repos/snow-rankings-v2`, using the identical original
brief and clean seed commit `901251e`. Its private repository is `hcekne/snow-rankings-v2`; the
earlier applications and both experiment records remain preserved. This is one new tool-assisted
build, not a matched causal comparison with the earlier timed control.

Future `run_build.py` coding launches use GPT-6.1-Sol with explicit **high** reasoning and have
**no development deadline**. The coding default changed on 30 October; earlier xhigh receipts
remain historical evidence and cannot be resumed under a different setting.
AnaxiGraph runs independently as `anaxigraph-snow-rankings-v2.service` at
`http://127.0.0.1:18782/mcp`. Its analysis settings are independent of the coding default; the
previously configured Sol/xhigh profile and `timeout_seconds: null` are not replaced by this
coding preference. The build first verifies real authorized MCP calls and
independent server-side effects; successful attachment alone is insufficient.

```sh
~/.local/state/anaxigraph/experiments/snow-rankings-v2/runtime/bin/python \
  experiments/snow_rankings/run_build.py --preflight-only
~/.local/state/anaxigraph/experiments/snow-rankings-v2/runtime/bin/python \
  experiments/snow_rankings/run_build.py \
  --preflight-proof ~/.local/state/anaxigraph/experiments/snow-rankings-v2/preflight.json
```

Coding and analysis occupy separate phases. At milestones 2 and 4 the coordinator waits for whole
mapping, every catalogue/module pair, and Fresh Eyes subsystem and final product reconciliation.
It preserves accepted batches when retrying failures. A fresh coding session then assesses the
saved alternatives, records accepted/rejected advice and applies useful improvements. Changed
implementations receive another complete stable analysis. Shared acceptance failures receive
repair attempts and remain incomplete until corrected. Git-only publication reuses compatible
analysis and verifies current whole-product coverage without paying to regenerate the same work.

Progress, coding usage and separately ingested analysis receipts live under
`~/.local/state/anaxigraph/experiments/snow-rankings-v2`. Unknown billed costs stay unknown.
Completed analyses are explicitly checked; a launched or interrupted workflow is never completion.
Structural coverage is currently Python/PYI; frontend JavaScript is fingerprinted but not parsed.

## Historical matched pair

This section and the committed preregistration describe the original Sol/xhigh pair. Future
`run_pair.py` launches use Sol/high and write their configured settings into a fresh state-local
manifest; a new run must not overwrite the original experiment's evidence.

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
python3 experiments/snow_rankings/run_pair.py --preflight-only
python3 experiments/snow_rankings/run_pair.py
```

Its defaults select `~/repos/snow-rankings` and `~/repos/snow-rankings-control`, four tasks of twenty
minutes each, and the frozen executable at
`~/.local/state/anaxigraph/experiments/snow-rankings-2026-10-26/runtime/bin/anaxigraph`.
Every task gets a fresh ephemeral GPT-6.1-Sol/xhigh context with ignored user configuration and
equal sandbox/tool settings. Only the treatment has an explicitly configured AnaxiGraph MCP server.
The server's authorized tools are preapproved using the scoped
`mcp_servers.anaxigraph.default_tools_approval_mode="approve"` setting; global
`approval_policy="never"` remains unchanged. This is the documented
[server-specific MCP approval setting](https://learn.chatgpt.com/docs/extend/mcp).
Before starting either coding arm, the launcher runs the real coding client against a one-file
fixture. It requires successful `scan` and `prepare_change` results plus independently observed
`task_started` and `context_supplied` server database events. It saves a bounded proof and server
evidence database outside the app repos. A failed probe prevents the trial from starting.
An inspected proof for the same frozen runtime can be reused with `--preflight-proof PATH`.
Completed MCP events containing `error` or `result.isError` count as failures, and only successful
`inspect` calls count as consulted context. Attachment and attempted calls do not prove delivery.
The original pilot's calls were all approval-blocked; its software and evidence are preserved, but
that pilot cannot estimate AnaxiGraph's effect.
Later task files are copied immediately before their turn; the nights change is withheld until task4.
The coordinator runs the shared checker against each app's `run.json` command on assigned localhost
ports, stops only owned processes and commits/pushes available work. A coding response does not count
as product completion if external acceptance fails. A single early provider failure may retry within
the same task deadline; later failures remain recorded. Sandboxing is never silently downgraded.
Both agents receive the actual twenty-minute budget and UTC deadline. New runs use corrected
contract checker2; `--assessment-version 1` reproduces the original checker. `--publish-branch`
selects the remote branch receiving each milestone HEAD, preserving existing app branches during
replications. `--experiment-id` defaults to the state directory name and also labels server-side
evidence. A state-local `manifest.json` takes precedence over the original manifest.

Progress and sanitized event metadata live outside the repos under the experiment state directory.
Per-arm event logs are capped at4MB; full source, command output and model transcripts are discarded.
Task, session, PID, timestamps, actual reported token usage and nullable cost remain available.

The original as-run checker and feedback remain frozen. `amendment-v2.json` records corrections to
unpublished integer/score/unknown/confidence assumptions, registered before milestone2 outcomes were
observed. Use the separate adjudicator against finished commits, without changing either agent:

```bash
python3 experiments/snow_rankings/adjudicate.py --through 1
# Once all tasks have finished:
python3 experiments/snow_rankings/adjudicate.py
```

It exports exact Git commits into temporary directories under home, uses isolated source paths with
the existing arm dependency environment, and writes `assessment-v2.json` beside the original results.
Both original and adjudicated scores remain visible. Both coding agents already received the original
failed milestone1 summary; that influence remains a limitation. The correction is not an AnaxiGraph
benefit. Qualitative quality, actual provider correctness and unavailable confidence comparisons
remain separately unmeasured.

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
