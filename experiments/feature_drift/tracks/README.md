# Scenario catalog

Adapted from the maintainer's Google prototype at commit `8373f5b`. The two JSON
specifications preserve **42 requests**: initial design (0) and twenty evolving
requests per product. Each has inline probes P1/P2/P3 at 7, 14 and 20. Later tasks
retain the effects of those probes; these are different from Switchboard's three
independent changes applied to copies of its final code.

| Track | Architectural pressure | Status |
| --- | --- | --- |
| Switchboard | Provider evolution, shared state, extension isolation and CI scope | Executable (`scenario/`) |
| ChronoWeave | Bitemporal ledgers, speculative merges, extensible verification and replay | Executable (`tracks/chronoweave/`) |
| AetherLoom | Discrete fields, protocol dialects, conserved flux and fault isolation | Executable (`tracks/aetherloom/`) |

A track folder is executable once it holds `track.json`, a `seed/` with `run.json`,
public requests `00`–`20` and a hidden acceptance file per request; otherwise the
JSON draft stays specification-only and execution is refused before any model or
state. The drafts remain the origin; `track.json` lists every adaptation, including:

- each request keeps its original title and prompt and adds a precise public
  contract (endpoints, errors, tie-breaking, numeric rules) without prescribing
  internal structure or file names;
- no network or new dependencies: zstd becomes gzip, Ed25519 and zero-knowledge
  proofs become HMAC/SHA-256, MCP becomes newline-delimited JSON-RPC over stdio;
- the CI request (5) uses Switchboard's `ci.json` selector contract, so the bare
  arm is never told about AnaxiGraph; the CI safety gate applies from step 5;
- probe requests 7, 14 and 20 do not tell the agent that they are probes.

Inspect, export or run:

```bash
uv run python experiments/feature_drift/runner.py --list-tracks
uv run python experiments/feature_drift/runner.py --track chronoweave --describe-track
uv run python experiments/feature_drift/runner.py --track aetherloom --state <new dir> \
  --arms bare,charter,anaxigraph --replicates 3
```

Exports contain the specification and its digest, not arm results. Use a new state
directory whenever frozen scenario, oracle, runtime or harness inputs change.

## Running a paid campaign

Earlier Switchboard campaigns used the runner defaults: Codex CLI, model `gpt-6.1-sol`,
reasoning effort `high`, three arms at concurrency 3, and scheduled AnaxiGraph
Patterns reviews at steps 6, 11 and 16. Only `--state`, `--arms`, `--replicates`
and `--concurrency` were passed. Runs since the CI safety gate also use its default
of 2 repair attempts. Keep those defaults for comparability. The agent
timeout defaults to the track's limit: 90 minutes for Switchboard, 2 hours for
the new tracks, whose requests are about twice as long.

```bash
# from the repository root, with `codex` logged in and `uv sync` done
.venv/bin/python experiments/feature_drift/runner.py --track chronoweave \
  --state <new state dir> --arms bare,charter,anaxigraph --replicates 3 --concurrency 3 \
  > <state dir>.log 2>&1
.venv/bin/python experiments/feature_drift/report.py --state <state dir>
```

- Use a new state directory outside the repository. Rerunning the same command
  resumes unfinished chains; changed inputs are refused rather than mixed.
- Do not edit or pull this checkout while a run is going: the anaxigraph arm uses
  its editable install as the runtime.
- The decision rule needs three matched replicates per arm; one replicate is a pilot.
- Do not commit raw run results; report the summary table and verdict.

## Qualification without models

`--agent ref:<git repo>` replays a reference implementation tagged `step-00`…`step-20`
through the real harness, CI safety gate included. Both references (kept outside
this repository) complete all 21 steps with every hidden check passing, no omitted
consumer obligation and no missed planted mutant. Deliberately broken variants of
each probe step fail that step's hidden checks.

## Comparison rule (fixed before any paid run on these tracks)

`chronoweave-v1` and `aetherloom-v1` apply the [Switchboard decision rule](../README.md)
unchanged, with one substitution: the probe measures (pass count and existing files
edited) come from the inline probe requests 7, 14 and 20 instead of post-history
probes. At least three matched replicates per arm are required.

AnaxiGraph's immediately executable [hypothesis tests](../../google_transfer/test_hypotheses.py)
cover the relevant tool risks today, without claiming either proposed product has
been implemented or that AnaxiGraph beats another arm.
