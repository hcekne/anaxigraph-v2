"""Executor qualification fails before inference; fixture runs need no Codex install."""

import concurrent.futures
import json
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import executor_policy  # noqa: E402
import runner  # noqa: E402


@pytest.fixture
def fake_executor(monkeypatch):
    calls = []
    executor_policy._verified.clear()
    monkeypatch.setattr(executor_policy.shutil, "which", lambda _: sys.executable)

    def run(argv, cwd, timeout):
        calls.append((argv, cwd, timeout))
        if "debug" in argv:
            payload = [
                {
                    "role": "developer",
                    "content": [
                        {
                            "text": "<permissions instructions>\n"
                            "`sandbox_mode` is `workspace-write`. Network access is enabled."
                        }
                    ],
                }
            ]
        else:
            payload = {
                "socketpair": True,
                "cross_thread_asyncio": True,
                "egress_denied": True,
                "private_read_denied": True,
                "workspace_read_write": True,
            }
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
            resource_usage={"termination_reason": "completed"},
        )

    monkeypatch.setattr(executor_policy, "run", run)
    yield calls
    executor_policy._verified.clear()


def test_exec_and_sandbox_share_named_deny_all_policy(fake_executor, tmp_path):
    config = SimpleNamespace(model="fixture-model", effort="high", runtime=tmp_path / "runtime")
    argv = runner.codex_argv(config, tmp_path, True)
    for key, value in executor_policy.settings().items():
        assert f"{key}={value}" in argv
    assert "--sandbox" not in argv and "-P" not in argv
    assert not any("sandbox_workspace_write" in arg for arg in argv)
    receipt = executor_policy.preflight()
    resolution, _, _ = fake_executor[0]
    assert "debug" in resolution and "-P" not in resolution and "--sandbox" not in resolution
    assert 'default_permissions="drift-local"' in resolution
    assert 'model_provider="drift-no-inference"' in resolution
    probe, cwd, timeout = fake_executor[1]
    assert probe[1] == "sandbox" and probe[probe.index("-P") + 1] == executor_policy.PROFILE
    for key, value in executor_policy.settings().items():
        if key != f"permissions.{executor_policy.PROFILE}":
            assert f"{key}={value}" in probe
    assert '="deny"' in next(x for x in probe if x.startswith("permissions.drift-local="))
    assert "exec" not in probe and timeout == 20
    assert not cwd.exists()
    assert receipt["models_started"] == 0 and receipt["policy"] == executor_policy.POLICY
    policy = receipt["policy"]
    assert policy["network"]["domains"] == {}
    assert policy["network"]["allow_upstream_proxy"] is False
    assert policy["network"]["allow_local_binding"] is False
    assert policy["filesystem"] == {":root": "read"}
    profile = tomllib.loads(
        "profile=" + executor_policy.settings()[f"permissions.{executor_policy.PROFILE}"]
    )["profile"]
    assert profile == {key: policy[key] for key in ("extends", "filesystem", "network")}


def test_parallel_sessions_share_one_successful_preflight(fake_executor):
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: executor_policy.preflight(), range(8)))
    assert len(fake_executor) == 2
    assert all(result == results[0] for result in results)


def test_ignored_default_permissions_fails_before_explicit_profile_smoke(
    fake_executor, monkeypatch
):
    def restricted(argv, *_):
        assert "debug" in argv, "An ignored default profile reached the functional probe"
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "role": "developer",
                        "content": [
                            {
                                "text": "<permissions instructions>\n"
                                "`sandbox_mode` is `workspace-write`. Network access is restricted."
                            }
                        ],
                    }
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr(executor_policy, "run", restricted)
    with pytest.raises(ValueError, match="profile resolution failed"):
        executor_policy.preflight()


@pytest.mark.parametrize("response", ["unsupported permission profile", "{}", "null"])
def test_unsupported_or_incomplete_preflight_refuses_paid_session(
    fake_executor, monkeypatch, tmp_path, response
):
    monkeypatch.setattr(
        executor_policy,
        "run",
        lambda *_: SimpleNamespace(returncode=0, stdout=response, stderr=""),
    )
    monkeypatch.setattr(
        runner.subprocess, "Popen", lambda *a, **k: pytest.fail("Started a paid model")
    )
    log = tmp_path / "session/step.jsonl"
    with pytest.raises(ValueError, match="no paid session started"):
        runner.run_agent(SimpleNamespace(agent="codex"), tmp_path, log, "fixture", False)
    assert not log.parent.exists()


def test_noop_and_copy_do_not_require_installed_codex(monkeypatch, tmp_path):
    monkeypatch.setattr(
        executor_policy, "preflight", lambda: pytest.fail("Fixture requested Codex")
    )
    for agent in ["noop", f"copy:{tmp_path / 'empty'}"]:
        (tmp_path / "empty").mkdir(exist_ok=True)
        result = runner.run_agent(
            SimpleNamespace(agent=agent),
            tmp_path,
            tmp_path / f"{agent[:4]}.jsonl",
            "fixture",
            False,
        )
        assert result["status"] in {"noop", "copied"}


def test_missing_codex_fails_before_any_subprocess(monkeypatch):
    monkeypatch.setattr(executor_policy.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="not installed"):
        executor_policy.preflight()


def test_main_preflight_failure_keeps_state_and_models_untouched(monkeypatch, tmp_path):
    state = tmp_path / "state"
    monkeypatch.setattr(
        sys, "argv", ["runner", "--state", str(state), "--arms", "bare", "--no-probes"]
    )

    def unsupported(*_):
        raise ValueError("Unsupported executor; no paid session started")

    monkeypatch.setattr(executor_policy, "preflight", unsupported)
    monkeypatch.setattr(runner, "run_agent", lambda *a: pytest.fail("Started a paid model"))
    monkeypatch.setattr(runner, "provenance", lambda *a: pytest.fail("Wrote run provenance"))
    with pytest.raises(SystemExit) as result:
        runner.main()
    assert result.value.code == 2
    assert not state.exists()


def test_same_private_paths_reach_each_arm_and_preflight(fake_executor, tmp_path):
    private = (tmp_path / "oracle", tmp_path / "other-arm")
    config = SimpleNamespace(
        model="fixture-model",
        effort="high",
        runtime=tmp_path / "runtime",
        private_roots=private,
    )
    for treatment in (False, True):
        argv = runner.codex_argv(config, tmp_path / "repo", treatment)
        configured = next(x for x in argv if x.startswith("permissions.drift-local="))
        value = tomllib.loads(configured)["permissions"]["drift-local"]
        assert all(value["filesystem"][str(path)] == "deny" for path in private)
    receipt = executor_policy.preflight(private)
    assert all(receipt["policy"]["filesystem"][str(path)] == "deny" for path in private)
    assert receipt["checks"]["private_read_denied"]
