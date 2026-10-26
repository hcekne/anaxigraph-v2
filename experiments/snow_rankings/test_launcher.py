"""Local launcher mechanics; synthetic CLI events do not establish model performance."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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
