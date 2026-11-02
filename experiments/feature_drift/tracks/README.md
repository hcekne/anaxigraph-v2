# Scenario catalog

Adapted from the maintainer's Google prototype at commit `8373f5b`. The two JSON
specifications preserve **42 requests**: initial design (0) and twenty evolving
requests per product. Each has inline probes P1/P2/P3 at 7, 14 and 20. Later tasks
retain the effects of those probes; these are different from Switchboard's three
independent changes applied to copies of its final code.

| Track | Architectural pressure | Status |
| --- | --- | --- |
| Switchboard | Provider evolution, shared state, extension isolation and CI scope | Executable existing campaign |
| ChronoWeave | Bitemporal ledgers, speculative merges, extensible verification and replay | Specification only |
| AetherLoom | Discrete fields, protocol dialects, conserved flux and fault isolation | Specification only |

Inspect and export without any model calls:

```bash
uv run python experiments/feature_drift/runner.py --list-tracks
uv run python experiments/feature_drift/runner.py --track chronoweave --describe-track
uv run python experiments/feature_drift/runner.py --track aetherloom --export-track /path/to/new-spec.json
```

Exports contain the specification and its digest, not arm results. Existing files
are not overwritten. Draft execution is rejected before a model or benchmark
starts. The existing [Switchboard protocol](../README.md) remains runnable; use a
new state directory when frozen scenario, oracle, runtime or harness inputs change.

## Acceptance work before a paid campaign

The imported oracle ideas need externally executable checks with explicit public
contracts. Preserve the ideas without guessing behavior hidden from the agent:

- ChronoWeave: historical repeatability after retraction/compaction; extension
  isolation through a declared capability surface; atomic promotion and quarantine
  when a parent is retracted concurrently.
- AetherLoom: conservation including sinks and quantization residuals; policy and
  quarantine transitions at tick boundaries; extensible dialect/auditor discovery.

Define wire operations, validation/errors, deterministic tie breaking, CRC variant,
fee brackets, numerical tolerances and checkpoint compatibility. Inject AetherLoom
faults mid-tick but apply quarantine at its documented boundary. A diamond-shaped
DAG is not itself a causal cycle: define the actual invalid-cycle injection.
Judge extension isolation without requiring particular internal filenames.

Then implement cumulative evaluator-owned checks for steps 0–20, exercise all six
fault probes against both valid and deliberately broken candidates, and run the
existing no-model harness before paying for matched coding arms. Pre-register a
comparison rule for each new track. No synthetic timing, winning assertion or
fixed weakness diagnosis from the prototype is included.

AnaxiGraph's immediately executable [hypothesis tests](../../google_transfer/test_hypotheses.py)
cover the relevant tool risks today, without claiming either proposed product has
been implemented or that AnaxiGraph beats another arm.
