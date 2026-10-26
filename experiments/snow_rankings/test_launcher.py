"""Local launcher mechanics; synthetic CLI events do not establish model performance."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import run_pair

HERE = Path(__file__).parent


class LauncherTests(unittest.TestCase):
    def test_commands_pin_model_and_isolate_mcp(self):
        root, directory, runtime = (
            Path("/home/test/repo"),
            Path("/home/test/state"),
            Path("/home/test/runtime"),
        )
        for arm in ("treatment", "control"):
            command = run_pair.command(root, arm, directory, runtime)
            self.assertEqual(command[command.index("--model") + 1], "gpt-6.1-sol")
            self.assertIn('model_reasoning_effort="xhigh"', command)
            self.assertIn("--ignore-user-config", command)
            self.assertIn("--ignore-rules", command)
            self.assertIn("--ephemeral", command)
            self.assertEqual(command[command.index("--sandbox") + 1], "workspace-write")
            has_server = any("mcp_servers.anaxigraph.command=" in item for item in command)
            self.assertEqual(has_server, arm == "treatment")
            self.assertIn('approval_policy="never"', command)
            scoped_approval = 'mcp_servers.anaxigraph.default_tools_approval_mode="approve"'
            self.assertEqual(scoped_approval in command, arm == "treatment")
        command = run_pair.command(root, "treatment", directory, runtime, experiment_id="new-trial")
        self.assertIn('mcp_servers.anaxigraph.env.ANAXIGRAPH_EXPERIMENT_ID="new-trial"', command)

    def test_mcp_delivery_checks_errors_instead_of_completed_status(self):
        for reply, error, expected in (
            (
                None,
                {"message": "MCP tool call requires approval, but approval policy is never"},
                False,
            ),
            ({"content": [], "isError": True}, None, False),
            (None, None, False),
            (
                {"content": [{"type": "text", "text": "private context"}], "isError": False},
                None,
                True,
            ),
        ):
            projected = run_pair.extract(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "mcp_tool_call",
                            "status": "completed",
                            "server": "anaxigraph",
                            "tool": "inspect",
                            "result": reply,
                            "error": error,
                        },
                    }
                ).encode()
            )
            self.assertEqual(projected["mcp_success"], expected)
            self.assertNotIn("private context", json.dumps(projected))
            if error:
                self.assertEqual(projected["mcp_failure_category"], "approval_blocked")

    def test_blocked_inspect_does_not_record_context_consulted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = root / "codex"
            fake.write_text("""#!/usr/bin/env python3
import json, sys
sys.stdin.read()
print(json.dumps({'type':'item.completed','item':{'type':'mcp_tool_call','status':'completed','server':'anaxigraph','tool':'inspect','error':{'message':'requires approval'}}}))
print(json.dumps({'type':'turn.completed','usage':{}}))
""")
            fake.chmod(0o755)
            with (
                patch.dict(os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"]}),
                patch.object(run_pair.trial, "record") as record,
            ):
                result = run_pair.coding(
                    root,
                    "treatment",
                    root / "state",
                    Path("/bin/true"),
                    "fixture",
                    time.monotonic() + 5,
                    {"task": "test"},
                    lambda: None,
                )
            self.assertEqual(result["mcp_successes"], 0)
            self.assertEqual(result["mcp_failures"], 1)
            self.assertFalse(result["treatment_delivered"])
            record.assert_not_called()

    def test_prompt_states_equal_budget_and_actual_deadline(self):
        for arm in ("treatment", "control"):
            prompt = run_pair.prompt_for(
                arm,
                1,
                18780,
                Path("/home/state"),
                None,
                minutes=20,
                deadline="2026-10-26T12:00:00+00:00",
            )
            self.assertIn("20 minutes", prompt)
            self.assertIn("2026-10-26T12:00:00+00:00", prompt)

    def test_milestone_push_targets_requested_remote_branch(self):
        with patch.object(run_pair, "git", side_effect=["", "revision", ""]) as git:
            result = run_pair.commit_and_push(
                Path("/home/repo"), 1, "experiment/verified-anaxigraph"
            )
        self.assertTrue(result["pushed"])
        self.assertEqual(result["branch"], "experiment/verified-anaxigraph")
        self.assertEqual(
            git.call_args.args[1:],
            ("push", "origin", "HEAD:refs/heads/experiment/verified-anaxigraph"),
        )

    def test_failed_real_client_preflight_prevents_both_coding_arms(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(
                    sys, "argv", ["run_pair", "--state", str(root), "--runtime", "/bin/true"]
                ),
                patch.object(run_pair.shutil, "which", return_value="codex"),
                patch.object(run_pair, "preflight", return_value={"status": "failed"}),
                patch.object(run_pair.concurrent.futures, "ThreadPoolExecutor") as workers,
            ):
                self.assertEqual(run_pair.main(), 1)
            workers.assert_not_called()
            self.assertFalse((root / "summary.json").exists())

    def test_new_trial_selects_corrected_contract_checker(self):
        config = run_pair.parser().parse_args([])
        self.assertEqual(config.assessment_version, 2)
        prompt = run_pair.prompt_for("treatment", 2, 18780, Path("/home/state"), None)
        self.assertIn("'fresh-eyes'", prompt)
        self.assertIn("workflow_status", prompt)

    def test_environment_and_event_projection_keep_only_permitted_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "unrelated-thread", "CLAUDECODE": "1"}):
                environment = run_pair.environment(Path(temporary))
            self.assertNotIn("CODEX_THREAD_ID", environment)
            self.assertNotIn("CLAUDECODE", environment)
            self.assertEqual(environment["HOME"], os.environ["HOME"])
            self.assertEqual(environment["TMPDIR"], str(Path(temporary) / "tmp"))
        projected = run_pair.extract(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "status": "completed",
                        "command": "secret source command",
                        "aggregated_output": "private source",
                    },
                }
            ).encode()
        )
        self.assertNotIn("secret", json.dumps(projected))
        self.assertNotIn("private", json.dumps(projected))

    def test_parallel_fake_workers_persist_usage_without_claiming_app_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binaries = root / "bin"
            binaries.mkdir()
            fake = binaries / "codex"
            fake.write_text("""#!/usr/bin/env python3
import json, sys
sys.stdin.read()
print(json.dumps({'type':'thread.started','thread_id':'synthetic-session'}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':3,'cached_input_tokens':0}}))
""")
            fake.chmod(0o755)
            for name in ("a", "b"):
                repo = root / name
                repo.mkdir()
                (repo / "initial.txt").write_text("same seed")
                subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
                subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repo),
                        "-c",
                        "user.name=Fixture",
                        "-c",
                        "user.email=fixture@example.invalid",
                        "commit",
                        "-qm",
                        "seed",
                    ],
                    check=True,
                )
            environment = dict(os.environ)
            environment["PATH"] = str(binaries) + os.pathsep + environment["PATH"]
            proof = root / "synthetic-proof.json"
            proof.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "runtime": str(Path("/bin/true").resolve()),
                        "model": "gpt-6.1-sol",
                        "reasoning_effort": "xhigh",
                        "server_events": ["task_started", "context_supplied"],
                        "coding": {"mcp_successes": 2, "mcp_failures": 0},
                    }
                )
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(HERE / "run_pair.py"),
                    "--state",
                    str(root / "state"),
                    "--runtime",
                    "/bin/true",
                    "--treatment",
                    str(root / "a"),
                    "--control",
                    str(root / "b"),
                    "--task-limit",
                    "1",
                    "--minutes",
                    ".1",
                    "--preflight-proof",
                    str(proof),
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            for arm in ("treatment", "control"):
                state = json.loads((root / "state" / arm / "progress.json").read_text())
                self.assertEqual(state["tasks"][0]["coding"]["input_tokens"], 10)
                self.assertEqual(state["tasks"][0]["coding"]["status"], "completed")
                self.assertEqual(state["status"], "finished_with_failures")
            comparison = json.loads((root / "state" / "comparison.json").read_text())
            self.assertIsNone(comparison["arms"]["control"]["coding"]["cost_usd"]["reported_sum"])


if __name__ == "__main__":
    unittest.main()
