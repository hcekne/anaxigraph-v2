# Working in AnaxiGraph V2

This is the standalone replacement core targeting the existing `anaxigraph` PyPI project at 1.0.
The legacy code lives separately in `~/repos/anaxigraph`; do not import its runtime.

Commit and push directly to `main` when finishing authorized work. Keep slow checks in CI rather
than adding pre-push gates. Run file-writing automation end to end before reporting it working.
Report measured results and distinguish local checks, CI, live model work and publication.

Use this checkout's uv environment:

```bash
uv sync --locked --extra dev
uv run pytest
uv run ruff check src tests
```

Keep the component boundaries in `docs/architecture.md`: shared records, indexing, storage,
analysis, workflow execution, application and interfaces. CLI, HTTP and MCP delegate to one application. Source parsing
does not execute target code; observations, interpretations, supplied check reports and proposals
retain different meanings. Store current understanding canonically with bounded retained state.

Fresh Eyes receives behavior only, without current source, identifiers or topology. Default pattern
review screens the catalogue cheaply and evaluates only selected opportunities in detail. A screen
is not a rating or proof of irrelevance. Preserve explicit exhaustive reviews as an opt-in mode;
every pair declared for detailed evaluation still needs a rating or unknown, tradeoffs and evidence.
Prefer clear ownership, small public contracts and independently testable components. Functions and
modular monoliths are valid choices; do not manufacture abstractions or force package extraction.

The maintainer selected a clean slate and selective MVP capabilities. Do not treat every legacy
feature as mandatory. Do not publish a release or alter the legacy service merely to move files.
