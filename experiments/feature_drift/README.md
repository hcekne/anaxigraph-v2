# Feature drift

Does AnaxiGraph keep a codebase healthy while it grows through many unplanned requests?

Agents one-shot a planned design well. Decay comes from the path A → A1 → … → A20, where each
request is reasonable on its own and nobody holds the overall shape. This benchmark replays that
path. One coding agent grows the same product through 20 requests in several arms, and neutral
tools measure behaviour, structure, CI cost and how easy the end result is to change.

## Scenario

**Switchboard** connects a business to outside services (weather, CRM, calendar, accounting,
internal MCP tools, ...) and exposes them through one HTTP API. It starts as a 60-line FastAPI
skeleton (`scenario/seed/`). The requests (`scenario/requests/`) arrive one at a time, written
like a product owner would: casual framing, an exact API contract and the provider's docs.
They are ordered to build design pressure:

| Steps | Pressure |
| --- | --- |
| 1–3, 5, 7 | One integration at a time: REST with keys, pagination, OAuth tokens, rate limits, MCP |
| 4, 9 | "Show everything connected", then "let the assistant discover and invoke everything" |
| 6 | CI with a machine-readable, safe and cheap test selector |
| 8, 12, 14, 15, 18, 19 | Cross-cutting rules for *every* integration: audit, quotas, caching, switching off, redaction, circuit breaking |
| 10 | Multi-tenancy arrives late and touches everything |
| 11, 20 | Providers change underneath: WeatherCo v1 retires, MCP servers change protocol |
| 13, 16 | Workflows over capabilities, then webhook-triggered workflows |
| 17 | Three new integrations at once |

A design with one integration contract and the cross-cutting rules applied once absorbs steps
8–20 cheaply. A design that grew integration by integration pays repeatedly. Nothing in the
requests prescribes the structure.

Hidden acceptance tests (`scenario/hidden/`) run the candidate from its `run.json` against
deterministic fake providers (`scenario/hub.py`). The agent sees only provider documentation.
After step *k*, the tests for steps 1..*k* run with providers at era *k*, so a WeatherCo or MCP
change breaks every earlier feature that was not migrated.

## Arms

All arms use the same model (default `gpt-6.1-sol`, effort `high`) through `codex exec`, a fresh
session per request, the same prompt, sandbox (no network), and timeout.

| Arm | Gets |
| --- | --- |
| `bare` | The request only |
| `charter` | The request plus the [design charter](charter.md) (rooms and interfaces, uniform plug-ins, CI follows architecture) |
| `anaxigraph` | Charter plus AnaxiGraph over MCP. Before every request the harness scans and refreshes semantic mapping; at steps 6, 11 and 16 it also runs Patterns and CI/CD reviews. The prompt asks for `prepare_change` → `assess_plan` → `validate_change`. |
| `anaxigraph-only` | AnaxiGraph without the charter: does the tool deliver the philosophy itself? |

`charter` is the bar AnaxiGraph has to clear: a static page of good advice costs nothing.

## Measures

After every step, with tools that do not use AnaxiGraph:

- **Behaviour:** hidden pass rate for the new request; cumulative pass rate; *regressions*
  (tests that passed at their own step and fail now).
- **Change amplification:** existing production files edited and lines changed per step.
  A rising curve is spaghetti made measurable.
- **Shape:** production size, largest module, function length and complexity, import cycles,
  fan-in/out and duplicated blocks.
- **CI (from step 6):** for every production file, the share of tests the agent's own selector
  picks versus the share that actually executes that file (per-test coverage), plus *planted
  bugs*. A mutation in one file that the suite catches must also be caught by the tests the
  selector picks for that file. A missed bug is an unsafe selection.
- **Cost:** agent wall time and tokens; AnaxiGraph refresh time and model calls.

After step 20, **probes** (`scenario/probes/`) give each final codebase three unseen requests:

1. a new REST integration;
2. a new MCP integration;
3. a cross-cutting setting.

The probe agent is a fresh session with the same model, no charter and no AnaxiGraph, so the
code alone is being judged. We measure probe pass rate, regressions, existing files edited and
the CI selection for the probe's diff.

## Decision rule (fixed before the full run)

Compare `anaxigraph` with `charter`, averaged over replicates (at least 3). AnaxiGraph is better
only if:

1. its final cumulative hidden pass rate is no more than 2 points below charter's, and
2. it meets at least two of:
   - **less change amplification:** mean existing production files edited per step over steps
     9–20 is at least 20% lower;
   - **easier to change:** more probe tests pass, or the same number pass with at least 20%
     fewer existing files edited;
   - **cheaper safe CI:** the mean selected-test share at step 20 is at least 20% lower, with no
     more planted bugs missed.

Report the time ratio (agent plus analysis, versus charter) beside the verdict. Above 1.5× the
benefit must be large to matter. `report.py` applies this rule mechanically. Results that miss
the rule are reported as "not shown", whatever else looks promising.

## Calibration

A reference implementation, written by a separate agent (Claude) from the request text alone,
passes all 115 hidden tests at step 20 including probes. Building it exposed five test and
specification bugs, which are now fixed, and several ambiguities, which the requests now
spell out. The reference itself is not committed, since agents could read it; it is kept in
`~/.local/state/anaxigraph/feature-drift/reference`. Its numbers are a yardstick, not a target:

| Measure | Reference |
| --- | ---: |
| Production / test lines | 2,320 / 1,821 |
| Largest module, import cycles, duplication | 214 lines, 0, 1.2% |
| Mean tests selected per changed file (CI) | 33% (15% actually execute the file) |
| Planted bugs missed by selection | 0 of 10 |

A five-request pilot (one replicate, GPT-6.1-Sol/high) ran all three arms cleanly. All arms
passed 29/29 hidden tests by step 5. AnaxiGraph made about 9 tool calls per request with no
failures and used about twice the bare arm's input tokens. The early requests are easy on
purpose; pressure starts at request 8.

## Run

```bash
# Harness check without models: a do-nothing agent.
uv run python experiments/feature_drift/runner.py --state /tmp/drift-smoke \
    --arms bare --steps 1-2 --agent noop --no-probes

# Pilot: three arms, first five requests, one replicate.
uv run python experiments/feature_drift/runner.py --state ~/drift/pilot \
    --arms bare,charter,anaxigraph --steps 1-5 --no-probes

# Full run: 20 requests plus probes, three replicates.
uv run python experiments/feature_drift/runner.py --state ~/drift/full \
    --arms bare,charter,anaxigraph --replicates 3 --concurrency 3

uv run python experiments/feature_drift/report.py --state ~/drift/full --json ~/drift/full/summary.json
```

Rerunning with the same `--state` resumes after the last completed step. Each chain keeps a git
history (`<state>/<arm>-r<n>/repo`), agent transcripts and prompts (`logs/`), and evaluation
artifacts (`eval/`). Pin the AnaxiGraph runtime (`--runtime`) for the whole run; its version and
commit are recorded.

A full run is about 20 × arms × replicates agent sessions (180 for three arms and three
replicates) plus 9 probe sessions per replicate, and AnaxiGraph refreshes. Run the pilot first.

## Limits

- One product, one language and one model. The scenario rewards one architectural idea, plug-in
  families with cross-cutting platform rules, because that is the failure we are testing for.
- The hidden tests encode a reading of each request. A reference implementation built from the
  request text alone was used to find tests that demand more than the text says.
- Planted bugs are simple single-site mutations, and coverage-based "executing tests" ignore
  dynamic effects at import time. Both measure selection, not suite quality alone.
- Agents could read this directory from the sandbox. Keep the benchmark outside candidate
  checkouts, and treat a transcript that mentions it as contaminated.
