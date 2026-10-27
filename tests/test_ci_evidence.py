"""CI declarations are bounded evidence, never executable project configuration."""

import json

import pytest

from anaxigraph.indexing import index
from anaxigraph.indexing.ci_evidence import (
    MAX_BYTES,
    MAX_FILES,
    MAX_JOBS,
    capture_ci_evidence,
    redacted_command,
)


def write(root, files):
    for path, value in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
    return index(root).snapshot.files


def test_github_jobs_triggers_commands_and_package_scripts_are_real_declarations(tmp_path):
    files = write(
        tmp_path,
        {
            ".github/workflows/ci.yml": """on:
  pull_request:
    paths: ['src/**']
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
defaults:
  run:
    working-directory: packages/payments
jobs:
  payments:
    strategy:
      matrix:
        python: ['3.11', '3.12']
    steps:
      - uses: actions/cache@v4
        with:
          path: .venv
          key: deps-${{ hashFiles('uv.lock') }}
      - run: uv run pytest tests/payments
  release:
    needs: [payments]
    if: github.ref == 'refs/heads/main'
    timeout-minutes: 10
    steps:
      - run: uv build
""",
            "package.json": json.dumps(
                {
                    "scripts": {
                        "test": "npm test --workspaces",
                        "test:payments": "vitest packages/payments",
                    }
                }
            ),
            "src/index.js": "throw new Error('never executed');",
        },
    )
    result = capture_ci_evidence(tmp_path, files)
    assert capture_ci_evidence(tmp_path, files, [])["pipelines"] == result["pipelines"]
    pipeline = result["pipelines"][0]
    assert pipeline["status"] == "parsed"
    assert pipeline["triggers"]["pull_request"]["paths"] == ["src/**"]
    assert pipeline["concurrency"]["cancel-in-progress"] == "true"
    payments, release = pipeline["jobs"]
    assert release["needs"] == ["payments"]
    assert release["timeout-minutes"] == "10"
    assert payments["strategy"]["matrix"]["python"] == ["3.11", "3.12"]
    assert payments["steps"][1]["run"] == "uv run pytest tests/payments"
    assert payments["steps"][1]["working_directory"] == "packages/payments"
    assert payments["steps"][0]["with"]["path"] == ".venv"
    assert result["package_scripts"]["scripts"][0]["command"] == "npm test --workspaces"
    assert result["source_files"][0]["line_count"] == 26
    assert result["timings"] == {"status": "not_supplied"}


def test_secrets_environment_and_credential_urls_never_leave_capture(tmp_path):
    files = write(
        tmp_path,
        {
            ".github/workflows/ci.yaml": """env:
  PRIVATE_VALUE: topsecretvalue
jobs:
  check:
    env:
      EXTRA: privatevalue
    steps:
      - run: API_KEY=hardcoded123 npm test
      - run: FOO=opaquevalue npm test
      - run: curl https://user:xyz987@example.com
      - uses: actions/upload-artifact@v4
        with:
          name: result
          password: notexposed
          arbitrary: notexposed2
""",
            "package.json": json.dumps(
                {"scripts": {"test": "OTHER=hiddenvalue npm test", "login": "password=hiddenpass"}}
            ),
        },
    )
    encoded = json.dumps(capture_ci_evidence(tmp_path, files))
    for value in (
        "topsecretvalue",
        "privatevalue",
        "hardcoded123",
        "opaquevalue",
        "xyz987",
        "notexposed",
        "hiddenvalue",
        "hiddenpass",
    ):
        assert value not in encoded
    assert "[value omitted]" in encoded
    assert "[URL omitted]" in encoded


def test_local_reusable_workflows_and_composite_actions_are_followed_but_remote_is_opaque(tmp_path):
    files = write(
        tmp_path,
        {
            ".github/workflows/main.yml": "jobs:\n  local:\n    uses: ./.github/workflows/common.yml\n  remote:\n    uses: vendor/repo/.github/workflows/test.yml@v1\n",
            ".github/workflows/common.yml": "jobs:\n  test:\n    steps:\n      - uses: ./.github/actions/check\n",
            ".github/actions/check/action.yml": "runs:\n  using: composite\n  steps:\n    - run: npm test\n      shell: bash\n",
        },
    )
    result = capture_ci_evidence(tmp_path, files, [".github/workflows/main.yml"])
    assert [item["id"] for item in result["local_references"]] == [
        ".github/workflows/common.yml",
        ".github/actions/check/action.yml",
    ]
    assert result["local_references"][1]["steps"][0]["run"] == "npm test"
    assert len(result["source_files"]) == 3


@pytest.mark.parametrize(
    "source",
    [
        "jobs: [",
        "not-a-mapping",
        "jobs: {}\njobs: {}",
        "jobs: &loop {test: *loop}",
        "jobs: " + "[" * 40 + "x" + "]" * 40,
    ],
)
def test_invalid_or_hostile_yaml_is_explicitly_unknown(tmp_path, source):
    files = write(tmp_path, {".github/workflows/ci.yml": source})
    pipeline = capture_ci_evidence(tmp_path, files)["pipelines"][0]
    assert pipeline["status"] == "unknown"
    assert "reason" in pipeline


def test_unsupported_missing_and_truncation_are_distinct(tmp_path):
    files = write(tmp_path, {"Jenkinsfile": "pipeline { stages {} }"})
    assert capture_ci_evidence(tmp_path, files)["pipelines"][0]["status"] == "unsupported"
    assert capture_ci_evidence(tmp_path, {})["pipelines"][0]["id"] == "project"
    sources = {f".github/workflows/{i:02}.yml": "jobs: {}" for i in range(MAX_FILES + 1)}
    sources[".github/workflows/00.yml"] = "jobs:\n" + "".join(
        f"  j{i}: {{steps: []}}\n" for i in range(MAX_JOBS + 1)
    )
    files = write(tmp_path, sources)
    result = capture_ci_evidence(tmp_path, files)
    assert result["pipelines_truncated"] is True
    assert result["pipelines"][0]["jobs_truncated"] is True
    assert len(result["pipelines"][0]["jobs"]) == MAX_JOBS
    (tmp_path / ".github/workflows/00.yml").write_text("#" * (MAX_BYTES + 1))
    oversized = capture_ci_evidence(tmp_path, files)["pipelines"][0]
    assert oversized["status"] == "unknown" and oversized["line_count"] is None


def test_exact_snapshot_identity_and_regular_paths_are_required(tmp_path):
    path = ".github/workflows/ci.yml"
    files = write(tmp_path, {path: "jobs: {}\n"})
    (tmp_path / path).write_text("jobs: {changed: {}}\n")
    with pytest.raises(ValueError, match="changed after mapping"):
        capture_ci_evidence(tmp_path, files)
    (tmp_path / path).unlink()
    (tmp_path / "outside").write_text("jobs: {}\n")
    (tmp_path / path).symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="could not be captured"):
        capture_ci_evidence(tmp_path, files)
    for filters in ([path, path], ["../elsewhere.yml"], "not-a-list"):
        with pytest.raises(ValueError, match="pipeline_paths"):
            capture_ci_evidence(tmp_path, files, filters)


def test_local_ci_selectors_are_fingerprinted_without_source_capture(tmp_path):
    files = write(
        tmp_path,
        {
            ".github/workflows/ci.yml": "jobs:\n  test:\n    steps:\n      - run: python scripts/select_tests.py && ./run-tests.sh\n",
            "scripts/select_tests.py": "raise RuntimeError('do not run or share this source')\n",
            "run-tests.sh": "printf 'not executed'\n",
        },
    )
    before = capture_ci_evidence(tmp_path, files)
    assert [item["path"] for item in before["script_references"]["files"]] == [
        "run-tests.sh",
        "scripts/select_tests.py",
    ]
    assert "do not run or share" not in json.dumps(before)
    (tmp_path / "scripts/select_tests.py").write_text("print('changed selector')\n")
    after = capture_ci_evidence(tmp_path, index(tmp_path).snapshot.files)
    assert before["script_references"] != after["script_references"]
    assert redacted_command("FOO=privatevalue uv run pytest") == "FOO=[value omitted] uv run pytest"


def test_yaml_tags_never_construct_python_objects_and_alias_expansion_is_bounded(tmp_path):
    sources = [
        "!!python/object/apply:os.system ['touch injected']",
        "a0: &a0 [x]\n"
        + "".join(f"a{i}: &a{i} [" + ",".join([f"*a{i - 1}"] * 20) + "]\n" for i in range(1, 7))
        + "jobs: {}\n",
    ]
    for source in sources:
        files = write(tmp_path, {".github/workflows/ci.yml": source})
        assert capture_ci_evidence(tmp_path, files)["pipelines"][0]["status"] == "unknown"
    assert not (tmp_path / "injected").exists()


def test_bounded_commands_matrices_and_step_counts_report_incomplete_capture(tmp_path):
    source = (
        "jobs:\n  test:\n    strategy:\n      matrix:\n        item: ["
        + ",".join(str(i) for i in range(21))
        + "]\n    steps:\n"
        + "".join("      - run: " + "x" * 301 + "\n" for _ in range(21))
    )
    files = write(tmp_path, {".github/workflows/ci.yml": source})
    pipeline = capture_ci_evidence(tmp_path, files)["pipelines"][0]
    job = pipeline["jobs"][0]
    assert pipeline["values_truncated"] is True
    assert job["steps_truncated"] is True
    assert len(job["steps"]) == 20
    assert len(job["steps"][0]["run"]) == 300
