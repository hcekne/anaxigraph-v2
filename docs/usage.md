# Using AnaxiGraph during development

AnaxiGraph is most useful when its understanding influences a real change. Use it to find the
right owner, reuse existing behavior, evaluate a smaller public interface, and choose the checks
that belong to that change. Ask for a deeper review when you want a second opinion on an area,
then give the findings you accept to the coding agent as concrete work.

## Install and run

The 1.0 package uses the existing `anaxigraph` PyPI project, with Python 3.11 or newer.
Check the [release record](releases/1.0.0.md) for publication status. With uv installed:

```bash
uvx --from anaxigraph==1.0.0 anaxigraph scan --repo /absolute/path/to/your/repo
uvx --from anaxigraph==1.0.0 anaxigraph status --repo /absolute/path/to/your/repo
uvx --from anaxigraph==1.0.0 anaxigraph serve --repo /absolute/path/to/your/repo
```

Open `http://127.0.0.1:8766`. Scanning uses no models; Mapping and other model workflows require an
authenticated, installed Codex or Claude CLI. For a persistent executable:

```bash
uv tool install 'anaxigraph>=1.0.0'
anaxigraph scan --repo /absolute/path/to/your/repo
uv tool upgrade anaxigraph
```

The installed requirement permits later stable releases. To keep an exact version, use the pinned
`uvx --from` form instead. Restart dashboard and MCP processes after upgrades. For source development,
run `uv sync --locked` in this repository and prefix commands with `uv run`; from another directory,
use `uv run --project /absolute/path/to/anaxigraph-v2 anaxigraph ...`. Always set `--repo`.

The dashboard's Repository chooser switches each browser tab independently. Add a local checkout
by its absolute path; its map, settings and saved analyses stay separate. The service's configured
MCP repository does not change when you switch the browser. The overview counts all mapped modules
and their source lines, excluding blank lines and comments. Structural refresh runs in the
background; reopening the page reconnects to its progress and the previous map remains available
until a new scan is saved. It does not start a semantic Mapping workflow or make model calls.

Evals & Benchmarks is a maintainer facility for developing AnaxiGraph. Its assets live under
`experiments/dashboard_evals`, outside the distributed application. It appears when a trusted
AnaxiGraph source installation analyses an AnaxiGraph checkout; ordinary project dashboards and
wheel installations omit it. The evidence ledger remains available through the CLI and API.
The raw JSON task debugger also lives there; independent request/submit operations remain
available through CLI, MCP and HTTP for every project. Normal workflow controls stay in each dashboard.

## Upgrading from 0.9.x

1.0 is a replacement implementation with breaking CLI, MCP and state boundaries. Existing scripts,
plugin commands and client configurations need updating. Commands such as `up`, `understand` and
`plan` are not 1.0 commands; start with `scan`, `serve`, and the current `--help`. Configure stdio MCP
with `uvx --from anaxigraph==1.0.0 anaxigraph mcp --repo /absolute/path/to/your/repo` and verify an
actual `scan` or `prepare_change` call. The [README](../README.md#connect-a-coding-agent) shows the JSON.

Before trying 1.0 on an existing checkout, stop the old service and back up its local database,
configuration and tracked `anaxigraph/` records outside that checkout. Preserve the old records
before exporting new ones into the same folder. 1.0 uses `.anaxigraph-v2/knowledge.sqlite` locally
and `anaxigraph/record.json` for portable analyses. It does not import legacy 0.9.x database or record
schemas automatically. Unsupported portable formats are rejected and preserved; compatible V2
portable records can be reused when their source and analysis identities match. Scan to build a
new structural map, then run Mapping or selected reviews to build new understanding.

After backing up state, upgrade an existing uv tool installation with
`uv tool upgrade 'anaxigraph>=1.0.0'`. The version constraint also replaces an old 0.9.x pin;
reconfigure and restart services separately.

Python/PYI parsing is supported. JavaScript and TypeScript files are read for their imports,
exports, top-level declarations and the uses of those names; types are not checked, member calls
are not resolved and path aliases are not followed. Other languages are fingerprinted only. The
replacement selectively implements useful capabilities; it does
not restore the legacy feature set or promise compatibility with old workflow outputs.

If an existing workflow still needs 0.9.x, temporarily run the old pinned package without replacing
your installed 1.0 command:

```bash
uvx --from anaxigraph==0.9.1 anaxigraph --help
```

Use the [legacy documentation](https://github.com/hcekne/anaxigraph/tree/v0.9.1) for its commands
and keep its state separate. Previous PyPI releases remain available; a package upgrade does not
migrate an already running legacy service.

## Everyday agent loop

1. **No scan is needed.** `prepare_change`, `assess_plan`, `validate_change`, `inspect`,
   `modules`, `reviews` and `tests_for_change` each read the checkout as it is: a file edited since the
   last call is read again, and only the files its change can affect are linked again. The
   first call in a checkout reads everything once; a new worktree starts from the facts another
   worktree of the same repository saved. `scan` remains for an explicit refresh and report:
   its `map_health` counts the imports between the checkout's own files that were linked and
   the source files left unread, and a `warning` there means dependency, consumer and test
   answers are incomplete. Structural reading uses no model calls. If semantic understanding
   is wanted and missing, run Mapping; compatible unchanged understanding is reused.
2. **Prepare** a substantial task with `prepare_change`. Keep its baseline and task identity.
   The reply opens with a `design_brief`: questions to answer before planning and any closed
   families near the task (see [component principles](component-architecture-principles.md)).
   It then names likely owners, contracts, test links and relevant saved recommendations.
   Use `inspect` to expand a module or saved review; `inspect("charter")` returns the general principles.
   Entity inspection starts compact; follow `next_offset` to page relationships, or pass `full=true`
   for complete entity annotations and contracts. Plan assessment returns changes and decisions;
   `include_brief=true` explicitly repeats the preparation brief.
3. **Assess the plan** before a substantial edit. `assess_plan` continues the prepared task even
   when its description is reworded. Adopt, defer or reject relevant material architecture findings
   from a review, with a reason. An extension-point finding, for a plan that adds a member to a
   family by editing its registrar, is advice under `architecture.advisory`: nothing is required,
   and a decision you do record for it is checked at validation. A family is a set of modules in
   one folder whose classes share a base class or protocol; sharing a helper module is not one.
   An accepted recommendation is a decision to do work, not evidence that it is done.
4. **Implement and check** the change. Prefer local behavior and public-contract tests; include
   consumers when contracts, behavior, shared resources or configuration can affect them. Keep the
   project's required checks.
5. **Validate** the candidate against the original baseline. Supply check commands, exit
   codes and the candidate snapshot ID. Explain unresolved findings or missing checks.
   Validation compares observed private accesses, cycles and consumers with the pinned baseline.
   Persisted proposal obligations receive narrow predicate checks where possible; adopting a
   proposal never establishes that its full behavior was implemented.

Start a separate task with a new `prepare_change`. Use `continue_task=true` or an explicit
`baseline_id` when continuing across changed wording or commits. Scanning alone does not advance
the pinned baseline. Source changes during a model run require a new run for the new snapshot; compatible accepted
batches are reused. Stronger models cannot fix stale inputs.

## Reviews are requested, not scheduled

Model reviews cost one or more model calls per module, so run one when you want its answer and
scope it to the modules in play. Nothing in AnaxiGraph schedules them.

| Work | When it is worth asking | What to do with the result |
| --- | --- | --- |
| Structural reading | Automatic on every call; no model calls | Ownership, references and exact checkout identity. |
| Semantic Mapping | When you want written summaries for the modules you are working in | Read sentences first; expand only relevant details. |
| Patterns and architecture | Before or after reshaping an area, scoped to that area | Inspect useful proposals and turn selected ones into implementation tasks. |
| Fresh Eyes | A milestone or a change of direction where an independent design is worth its cost | Compare all three alternatives with requirements and current source before choosing changes. |
| CI/CD | When component boundaries, shared build inputs or workflows change, or CI is slowing down | Validate scope, improve the pipeline and update check declarations. |

A whole-repository run on a large codebase is a deliberate, paid action: Mapping and pattern
screening each take one model call per production module. Completed batches are saved
immediately; resume unfinished work instead of starting everything again.

Default semantic Mapping covers executable production modules. Tests and inert namespace markers
remain in the structural map; use explicit module scopes or configuration `include_tests: true`
to annotate them too. Progress reports needed/reused/excluded module counts. Unchanged compatible
annotations need no model calls; explicit saved model and review choices remain part of reuse.

Pattern runs include catalogue screening, detailed review of selected opportunities, and focused
workers for boundaries, package/repository options, independent verification and CI/CD. They do
not run Fresh Eyes. Fresh Eyes receives behavior summaries rather than current source or topology,
and keeps three design alternatives at subsystem and product levels.

Defaults use Luna/low for mapping and discovery, and Sol/medium for selected details and positive
architecture/CI finding review. Existing explicit model choices are preserved. Global ordered
review passes intentionally replace dedicated finding reviewers; the effective policy is shown
in settings and run status. A blank timeout means unlimited waiting, while a finite value sets a
per-call deadline. Inspect measured attempts and failures before changing models or concurrency.

## Instructions for your project's agents

Adapt this block for the target repository's `AGENTS.md` or equivalent instructions:

```text
Use AnaxiGraph for substantial code changes:
- Prepare the task before editing; no scan is needed first. Keep the original baseline.
- Inspect likely owners, public contracts and relevant saved recommendations. Reuse existing capabilities.
- Assess substantial plans. Record adopt/defer/reject reasons for material architecture findings.
- Keep components cohesive, public interfaces small, implementation details private and tests independently runnable.
- After editing, execute the appropriate project checks, then validate against the original baseline.
- Run Patterns or Fresh Eyes only when asked or when you want a second opinion; scope the run to the modules you are changing.
- Revisit CI/CD when architecture or shared build inputs change. Use declared check inputs; widen for contracts, consumers and unresolved impact.
- Turn accepted recommendations into concrete tasks. Report what was changed, deferred or rejected and why.
- Report whether AnaxiGraph helped: which advice you used, what it changed, wasted output/calls, and anything important it missed.
```

Reviews are a team decision. You can ask an agent to conduct a review and implement selected
improvements in one task. It should inspect source, test the change and report the actual result.

## Tests for a change

AnaxiGraph can say which test files a checkout needs, from a record of what each test ran and
read. Your project produces the recording by running its own suite; AnaxiGraph never runs tests.

Start a recording before testing, then run your suite with per-test coverage and the reads
recorder. The manifest binds the run to its original source, and the recorder fingerprints the
finished coverage and JUnit outputs. Keep generated reports outside source inputs:

```bash
anaxigraph record-tests --start --suite backend --scope backend/
anaxi_recording_dir="$PWD/.anaxigraph-v2/recordings"
export ANAXIGRAPH_RECORDING="$anaxi_recording_dir/backend.json"
export ANAXIGRAPH_READS="$anaxi_recording_dir/backend-reads.json"
export COVERAGE_FILE="$anaxi_recording_dir/backend.coverage"
(
  cd backend
  PYTHONPATH="$(anaxigraph tests --recorder)" python -m pytest tests \
    -p anaxigraph_reads --cov=app --cov=migrations --cov-context=test --cov-report= \
    --junitxml="$anaxi_recording_dir/backend-junit.xml"
)
anaxigraph record-tests --coverage "$COVERAGE_FILE" \
  --junit "$anaxi_recording_dir/backend-junit.xml" --reads "$ANAXIGRAPH_READS" \
  --suite backend --scope backend/ --run-root backend
```

A complete recording covers one pytest invocation, including its declared pytest-xdist workers.
Missing, interrupted or mixed worker reports, source changes and replaced output files are
rejected before the last valid record is replaced. Early termination such as `--maxfail=1` is
incomplete even when pytest exits with an ordinary test-failure status. Source identities are
checked before and after testing and again when saved; this is project-produced evidence,
not independent attestation of execution or complete behavioral coverage.

Coverage shows the code each test ran. Name every folder the suite loads code from with
`--cov`, also one that a tool loads by path, such as database migrations: coverage then lists
the files no test loaded as well, and a file it was not measuring cannot be told from those.
The reads recorder adds what coverage cannot see: the files a test read as data, the folders
it scanned, whether it started another process, and which modules the run loaded at all. It
uses the standard library and pytest, so it runs in your test environment without
AnaxiGraph installed there; `anaxigraph tests --recorder` prints its folder. It writes
`.anaxigraph-reads.json` where the run started (`ANAXIGRAPH_READS` names another place), one
file per worker under pytest-xdist, and takes the nearest folder with a `.git` entry as the
checkout (`ANAXIGRAPH_ROOT` names it where there is none). Separately launched or multi-machine shards need a shared completion protocol before their
combined reports can qualify a narrow selection; the current recording contract does not
provide that protocol.

`--scope` names the folders the suite depends on, and `--run-root` the folder it ran in. The
record lands in `.anaxigraph-v2/test-records/`; carry it between machines as a CI artifact.
A worktree without one uses the record of the worktree that recorded most recently.

Module inspection also shows recorded file-level test consumers, with pagination, baseline
provenance and changed-source/test indicators. The dashboard links those tests from the module
panel. These observations help examine coupling; they are not complete current CI scope.

Ask with the `tests_for_change` tool, or in CI with `anaxigraph tests --paths`, which prints one
test file per line and nothing when the whole suite is needed. The record is compared with the
checkout itself, so everything that differs from the recorded run counts and the selection
widens by itself as the record ages. A test file is selected when:

- a recorded test in it executed a changed source file;
- it read a changed file as data, wherever in the checkout that file is, or scanned the folder
  a file appeared in or left. Code that reads a file once and keeps it serves other tests
  without reading again, so the tests that ran the code which did the reading are selected too;
- what a changed file does at import changed (module-level values, signatures, class bodies,
  cached functions, functions that set shared state, and any function the recorded run saw
  running outside a test), and the test imports the file or executed a module that uses it.
  The same holds again for every module whose own import-time code uses the file, since such
  a module passes the change on to its own users. Data read while a module was imported
  counts as part of that module. A file the record has no measurement of, because it is new
  or coverage was not measuring it, is treated this way for any change;
- it changed itself or is new, a helper it imports changed, or a `conftest.py` above it changed;
- the record cannot name its inputs: it executed no recorded source and read no recorded
  file, or it started a process whose reads are not seen (a `git` command working outside
  the checkout does not count). The tests of a measured process-starting helper also run, since
  it may keep the result for later callers. These run whenever anything changed. An opaque
  start outside a test, or in code coverage did not measure, requires the whole suite.

The whole suite is selected, with the reason, when a changed input cannot be attributed:

- a file inside the scope that nothing in the recorded run read, such as a container file,
  or a file the test run itself read;
- a dependency list or lock file inside the scope (`requirements*.txt`, `constraints*.txt`,
  `pyproject.toml`, `setup.py`, `setup.cfg`, `Pipfile`, `*.lock`): the suite is installed from
  it before any test runs, so a test that reads it does not stand for what depends on it;
- a Python file that was removed: what still imports it is not in the record;
- a module that nothing imports and whose folder nothing scanned, when it is new, was not
  measured, or changed what it does at import. Something other than an import statement loads
  such a file, and the record cannot say for which tests. The same holds when following the
  users of a changed file ends at such a module.

Prose, source in another language and files outside the scope select only the tests that read
them. A Python file that the recorded run did not load selects nothing.

A legacy record or one made without source-bound `--reads` remains readable, but selects the
whole suite after a recorded input changes. Its underlying coverage-only rules additionally mean: a changed file inside the
scope that the suite cannot import (data, configuration, source in another language) selects
the whole suite; files outside the scope are not compared at all; and only a file that coverage
lists without a line counts as not loaded, which takes the `--cov` setting above. Run AnaxiGraph
under the same Python version when it saves the record and when it is asked: the identity of
what a file does at import comes from that version's parser, so another version treats every
changed file as changed at import. That is safe and selects more.

Run the whole suite on your main branch regardless. These are not seen:

- a test that only checks whether a file exists;
- state that one test or a session-wide fixture sets up and another test uses;
- a module-level import whose name the importing file also assigns;
- a module that, when imported, changes what another module keeps, for the tests that only ask
  that other module;
- a file that a build step turns into something the tests use, when a test also reads the file
  itself;
- a file AnaxiGraph does not read at all: one that git ignores, a database file, a file over
  2 MB, or anything inside `dist`, `build` or `node_modules`.

In CI, a nightly job on the main branch records the suite and keeps the record as an artifact
(`.anaxigraph-v2/` is a hidden folder; artifact steps often skip those unless told otherwise).
A pull-request job fetches the latest record, runs `anaxigraph tests --paths`, and runs the
listed files, or everything when nothing is listed. Start in shadow mode: keep running the whole
suite and pass its JUnit reports to `anaxigraph tests --ran junit.xml --reads candidate-reads.json`.
Start a new recording for that candidate before testing, but keep the original test record for
selection; do not replace it with the candidate recording before the comparison. The candidate
reads report binds JUnit to current source and a completed invocation. Without it the comparison
is explicitly unverified. Missing previously recorded test files make it incomplete, and an
unmatched testcase is rejected rather than silently ignored. Each suite then carries a
`shadow` entry that names, under `missed`, every failing test file the selection would have left
out. Baseline failures are compared by testcase identity, so a new failure in an already-failing
file remains visible. A legacy baseline without testcase identities subtracts no failures. Let the selection skip tests
only after the misses have stayed empty on real pull requests.

## CI/CD and portable records

The CI/CD review reads pipeline declarations and proposes improvements. The earlier declared-input
planner (`ci-plan`, `anaxigraph/ci-policy.json`) was retired from the product in November 2026:
no project in its trials authored the policy it needs. Its code and tests live in
`experiments/declared_ci/` for the drift campaign that qualified it; use `tests_for_change`.
The [policy reference](change-request-ci-delivery.md#declare-accepted-tasks) explains task inputs,
ordering, consumed outputs, fallbacks, cache keys and release/scheduled checks.

CI reviews capture bounded literal runner/selector bodies and local Python script imports, including
their working directories. Redaction, missing scripts and capture limits remain explicit. To supply
observed test-selection evidence, use the separate experimental collector from this checkout:

```bash
uv run python experiments/feature_drift/collect_evidence.py \
  --repo /path/to/repo --output /path/to/repo/anaxigraph/ci-evidence.json
```

The collector explicitly runs that repository's `.venv` tests and selector from `ci.json`; its test
environment needs pytest-cov. The AnaxiGraph service never runs project tests. The canonical report
refreshes atomically after success; other output paths must be new. Add `--measure-diff --changed path`
(repeat `--changed` for the complete diff) to time full and selected checks on the same source.

To make selector safety a required CI check, include the complete changed-path set:

```bash
uv run python experiments/feature_drift/collect_evidence.py \
  --repo /path/to/repo --output /path/to/repo/anaxigraph/ci-evidence.json \
  --changed src/payments.py --changed tests/test_payments.py --check
```

This experiment performs coverage collection once and fails its own check on gaps; timing
comparisons remain optional. The supplied-evidence audit, gate and declared planner now live in
`experiments/declared_ci/`. The feature-drift runner applies that gate to product validation
externally. Ordinary `prepare_change`, CI review and `validate_change` do not require the
experiment's `ci.json` or `ci-evidence.json` protocol. The old `--runtime-context` and
`--require-ci-evidence` validation options have been removed; use the experimental collector's
`--check` for that protocol. Product validation still attributes current, stale and failed
check receipts; recorded test selection and prospective shadow checks remain separate evidence.


Commit `anaxigraph/record.json` after full workflows or explicit export. A nightly cloud job can
pull a repo, run its selected reviews, export and commit the record. Another clone scans locally
and reuses analyses whose inputs still match. Mapping, exporting, and committing/pushing are
separate operations. Ignore `.anaxigraph-v2/`, which contains local indexes, progress and trial data.

## Learning from real use

The [audit follow-up](audit-followup-2026-10-27.md) records current fixes and earlier trial measurements.

Periodically ask coding agents what they actually consulted, which recommendations changed their
implementation, which were rejected, and what the tool failed to find. Ask for examples and source
references rather than general impressions. Check whether the next change was easier, whether
required checks stayed useful, and whether CI feedback got faster without missed regressions.

The local evidence ledger distinguishes supplied context, inspected entities, model usage and
reported outcomes. `anaxigraph evidence --repo /absolute/path/to/your/repo` reads it; add `--export` to save a local report.
Retention is bounded and reports dropped history. Missing provider prices stay unknown.
Use this alongside agent interviews and project outcomes; inspection alone does not show that an
agent acted on advice.

The AnaxiGraph maintainer dashboard's **Evals & Benchmarks** workspace browses this ledger, filters loaded
pages by experiment/arm/task, and downloads its JSON. It also opens reports generated by
`uv run python experiments/feature_drift/report.py --state PATH --json report.json`.
Comparison cards retain reported qualification, consumer recall and missed mutants alongside
CI selection; missing measurements remain unknown. Loading a file starts no model calls and
does not establish its authenticity.

The [scenario catalog](../experiments/feature_drift/tracks/README.md) preserves ChronoWeave
and AetherLoom's initial design, twenty follow-up requests and three inline fault probes each.
These are specifications awaiting complete acceptance checks, not measured campaigns.
Use `uv run python experiments/feature_drift/runner.py --list-tracks` to inspect readiness.
The [dashboard smoke](../experiments/dashboard_evals/README.md) and
[adversarial regressions](../experiments/google_transfer/test_hypotheses.py) run without paid models.

For a controlled comparison, use the [architectural outcome experiment](../experiments/architecture_quality/README.md).
Its discovery track starts from identical coupled code and natural feature requests, freezes
Patterns/Fresh Eyes advice, then gives fresh agents unannounced later changes. Independent checks
verify behavior, persisted-state compatibility and safe CI scope; blind patch review judges the
chosen boundaries and contracts. A separate guided track checks prescribed component isolation.
Ordinary coding and a generic architecture-review control are supported. Tokens, latency and
additional analysis work remain separate from quality.

For longer integration and CI/CD histories, use the
[integration drift benchmark](../experiments/drift/README.md). It carries earlier code and state
into later tasks, separates safety from work avoided, and includes faults that an unsafe selector
must catch. Future coding launches default to **GPT-6.1 Sol / high**. Product analysis profiles and
previously frozen experiment results retain their separately recorded settings.

`prepare_change` needs a structural scan, not a full model mapping. Its three owner candidates
combine source text, paths/symbols and current saved understanding, with matching evidence.
Historical associations show files that changed together before the task baseline; inspect them
for shared obligations or weak boundaries without assuming they must change or widen CI.
Workflow status retains compact failure diagnostics after older progress files rotate away.

## Connections

Use explicit repository paths for stdio MCP. A dashboard service also exposes HTTP MCP at `/mcp`.
Test an actual tool call after configuring client permissions; attaching the server does not prove
the client permits unattended calls.

Default `serve` stays on `127.0.0.1:8766` and needs no authentication setup. Local browser requests
must have a trusted Host and matching Origin. For remote use, prefer an SSH tunnel to that loopback
service, such as `ssh -L 18766:127.0.0.1:8766 user@server`; open `http://127.0.0.1:18766` locally.

For a shared service behind an HTTPS reverse proxy, explicitly trust its public URL and configure
a random token of at least 32 characters:

```bash
export ANAXIGRAPH_HTTP_TOKEN="$(uv run --no-project python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uvx --from anaxigraph==1.0.0 anaxigraph serve --repo /path/to/repo --host 0.0.0.0 \
  --allow-origin https://anaxigraph.example.com
```

Replace the example hostname with the proxy's exact scheme, hostname and port; paths and wildcards
are not origin patterns. The proxy must preserve the public Host and HTTPS scheme. Repeat
`--allow-origin` for additional trusted server URLs. Store the token in the service's protected
environment, outside Git and URLs. Token authentication does not encrypt HTTP; use HTTPS or an
encrypted tunnel for remote traffic.

The browser prompts for HTTP Basic authentication: username **`anaxigraph`**, password **the token**.
API and HTTP MCP clients can instead send `Authorization: Bearer TOKEN` to the public URL, including
`https://anaxigraph.example.com/mcp`. Authentication and Host/Origin checks cover the dashboard,
REST API and MCP; binding to a LAN address without a token and allowed origin is refused.
