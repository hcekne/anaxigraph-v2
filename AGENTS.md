# Working in AnaxiGraph V2

This standalone replacement targets the existing `anaxigraph` PyPI project at 1.0. The legacy
checkout is `~/repos/anaxigraph`; do not import its runtime or change its services for V2 work.
Build useful capabilities selectively rather than restoring every legacy feature.

Use GPT-6.1 Sol with high reasoning effort as the default coding agent. Record explicit overrides
and the actual settings when available. This coding preference does not replace the product's
separate mapping, screening and architectural-review model profiles or historical trial records.

Commit and push directly to `main` when finishing authorized work. Keep slow checks in CI,
not pre-push. Exercise file-writing automation end to end. Distinguish local checks, CI,
live model results, deployment and publication. Do not publish a release unless requested.

Use this checkout's uv environment:

```bash
uv sync --locked --extra dev
uv run pytest
uv run ruff check src tests
uv run mypy
uv run python scripts/quality.py
```

Keep the boundaries in [architecture.md](docs/architecture.md). One Application serves CLI,
MCP and HTTP. Parsing never executes project code. Facts, inferred understanding, proposed
changes and supplied check reports retain distinct provenance. Store current understanding
canonically with bounded retention; portable records carry analyses, not rebuildable caches.

Prefer cohesive components, small public behavioral contracts and independent tests. Local or
separate-repository packages can compose in process. Functions and modular monoliths are valid;
do not manufacture abstractions or services. Shared data/configuration can widen impact even
with unchanged signatures. See [component principles](docs/component-architecture-principles.md).
Ask whether another thousand similar requests would preserve those boundaries. Provider additions
should use capability contracts, with private API/MCP details and focused executable verification;
shared contracts, authorization, state and build inputs must widen the affected scope when needed.

Fresh Eyes receives behavior only, without source identities or topology, and retains three
alternatives. Default Patterns screen cheaply, review selected opportunities, and include
architecture scouts plus CI/CD. Preserve explicit exhaustive/selected reviews and portable
identities. Material plan findings need adopt/defer/reject reasons; decisions do not prove implementation.

Use the [recommended routine](docs/usage.md): scan and prepare a substantial task, inspect and
assess its plan, then scan/check/validate against the original baseline. Run Patterns after
meaningful changes, Fresh Eyes at milestones and regularly on active projects, and CI review
when component or pipeline assumptions change. Judge usefulness through actual coding outcomes
and feedback from agents. Keep the README introductory and link deeper operational details.

Work within the current source budget. Simplify duplicated machinery before adding layers;
do not silently raise limits or pack code to game the count. Report measured size changes.
