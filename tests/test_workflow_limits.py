"""Exercise explicit unlimited calls and worker reasoning without paid providers."""

from __future__ import annotations

import signal

import pytest
from test_workflows import answer, app

from anaxigraph.workflows import providers
from anaxigraph.workflows.engine import WorkflowRunner


def test_unlimited_configuration_round_trips_and_reaches_worker_and_evidence(tmp_path):
    application = app(tmp_path, count=1)
    observed = []

    def provider(request, worker, *, timeout):
        observed.append((worker, timeout))
        return answer(request)

    runner = WorkflowRunner(application, provider)
    config = {
        "timeout_seconds": None,
        "stages": {
            "annotate": {
                "provider": "codex",
                "model": "fixture",
                "reasoning_effort": "xhigh",
            }
        },
    }
    runner.configuration(config)
    saved = WorkflowRunner(application, provider).configuration()
    assert saved["timeout_seconds"] is None
    assert saved["stages"]["annotate"]["reasoning_effort"] == "xhigh"
    result = runner.start("mapping", background=False)
    assert result["status"] == "completed", result
    assert observed == [
        ({"provider": "codex", "model": "fixture", "reasoning_effort": "xhigh"}, None)
    ]
    event = application.evidence.events()["items"][-1]["payload"]
    assert event["reasoning_effort"] == "xhigh"
    assert event["reasoning_effort_basis"] == "explicit-cli-config"


@pytest.mark.parametrize("timeout", [0, False, True, 29, 3601, "unlimited", 30.0])
def test_invalid_timeout_does_not_replace_saved_settings(tmp_path, timeout):
    runner = WorkflowRunner(app(tmp_path))
    runner.configuration({"timeout_seconds": None})
    with pytest.raises(ValueError, match="timeout_seconds"):
        runner.configuration({"timeout_seconds": timeout})
    assert runner.configuration()["timeout_seconds"] is None


@pytest.mark.parametrize(
    "worker",
    [
        {"provider": "claude", "reasoning_effort": "xhigh"},
        {"provider": "codex", "reasoning_effort": "unlimited"},
        {"provider": "codex", "reasoning_effort": True},
    ],
)
def test_unsupported_reasoning_is_rejected_before_cli_launch(tmp_path, worker, monkeypatch):
    runner = WorkflowRunner(app(tmp_path))
    with pytest.raises(ValueError, match="reasoning_effort"):
        runner.configuration({"stages": {"annotate": worker}})
    monkeypatch.setattr(
        providers, "_verified", lambda *_: pytest.fail("Invalid worker must not invoke a CLI")
    )
    with pytest.raises(ValueError, match="reasoning_effort"):
        providers.execute({}, worker)


class Process:
    pid = 42

    def __init__(self):
        self.returncode = None
        self.polls = 0

    def poll(self):
        self.polls += 1
        if self.polls > 1:
            self.returncode = 0
        return self.returncode

    def wait(self, *, timeout):
        self.returncode = -signal.SIGTERM


def test_unlimited_wait_does_not_consult_an_elapsed_deadline(monkeypatch):
    process = Process()
    monkeypatch.setattr(
        providers.time, "monotonic", lambda: pytest.fail("Unlimited calls have no deadline")
    )
    monkeypatch.setattr(providers.time, "sleep", lambda *_: None)
    providers._wait(process, [], None)
    assert process.returncode == 0
    assert process.pid not in providers._ACTIVE_OUTPUT


def test_finite_wait_still_terminates_a_timed_out_process_group(monkeypatch):
    process = Process()
    monkeypatch.setattr(process, "poll", lambda: process.returncode)
    moments = iter([0, 2])
    monkeypatch.setattr(providers.time, "monotonic", lambda: next(moments))
    killed = []
    monkeypatch.setattr(providers.os, "killpg", lambda *args: killed.append(args))
    with pytest.raises(ValueError, match="exceeded 1 seconds"):
        providers._wait(process, [], 1)
    assert killed == [(42, signal.SIGTERM)]
    assert process.returncode == -signal.SIGTERM
    assert process.pid not in providers._ACTIVE_OUTPUT


def test_unlimited_wait_retains_output_cap_and_process_cleanup(tmp_path, monkeypatch):
    process = Process()
    monkeypatch.setattr(process, "poll", lambda: process.returncode)
    oversized = tmp_path / "oversized.json"
    with oversized.open("wb") as stream:
        stream.truncate(providers.OUTPUT_BYTES + 1)
    killed = []
    monkeypatch.setattr(providers.os, "killpg", lambda *args: killed.append(args))
    with pytest.raises(ValueError, match="budget"):
        providers._wait(process, [oversized], None)
    assert killed == [(42, signal.SIGTERM)]
    assert process.pid not in providers._ACTIVE_OUTPUT
