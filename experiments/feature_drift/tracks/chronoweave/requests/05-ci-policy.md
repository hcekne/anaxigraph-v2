# Request 5 — Declared Component CI Policy & Fast Verification Path

Configure deterministic test target selection so edits to a single rule or verifier run only its component and contract tests in <1s.

Every change runs every test today, and the replay-heavy tests are getting slow. I don't want
a one-line change to cost a full run, ever. Set up CI now:

- A GitHub Actions workflow: on pull requests run lint plus only the tests a change can
  affect; on pushes to `main` (and nightly) run everything.
- A machine-readable selector our tooling can call, kept correct as the code changes.

## What I need

`ci.json` at the repository root: `{"select": ["<argv>", "..."]}`. Our tooling runs that command
from the repository root with `.venv/bin/python` available and no network. It writes the
changed paths (repository-relative, one per line) to stdin and reads pytest targets from stdout:
one per line (test files, directories or node ids), or the single line `ALL` to run everything.

- It must be **safe**: if a change could break a test, that test must be selected. Our tooling
  will regularly plant small bugs in single files and check the selected tests catch them.
- It should be **cheap**: select as little as safety allows. A change limited to one component
  (one rule, one verifier, one resolver, …) should run that component's tests and the contract
  tests that cover it, not the whole suite. Keep those selected tests fast (about a second).
- Changes only to Markdown files or `docs/` select nothing (empty output).
- Changes to `pyproject.toml`, `uv.lock` or `ci.json` → `ALL`.
- Every other change (any source file) selects at least one target or `ALL`; a changed test file
  (`tests/**/test_*.py`) selects itself (or `ALL`).
- It must finish in under 10 seconds, and every target it prints must exist.
