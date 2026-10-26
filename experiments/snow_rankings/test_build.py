"""Synthetic checks for the durable, unlimited single-repository build coordinator."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import run_build


class BuildLauncherTests(unittest.TestCase):
    def test_command_uses_pinned_model_and_durable_http_server(self):
        args = run_build.command(
            Path("/home/test/repo"), Path("/home/test/state"), "http://127.0.0.1:18782/mcp"
        )
        self.assertEqual(args[args.index("--model") + 1], "gpt-6.1-sol")
        self.assertIn('model_reasoning_effort="xhigh"', args)
        self.assertIn('approval_policy="never"', args)
        self.assertIn('mcp_servers.anaxigraph.default_tools_approval_mode="approve"', args)
        self.assertIn('mcp_servers.anaxigraph.url="http://127.0.0.1:18782/mcp"', args)
        self.assertTrue({"--ignore-user-config", "--ignore-rules", "--ephemeral"} <= set(args))
        self.assertFalse(any("mcp_servers.anaxigraph.command=" in item for item in args))

    def test_unlimited_worker_records_only_successful_mcp_calls_as_delivery(self):
        temporary_root = Path.home() / ".cache/anaxigraph/tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as temporary:
            root = Path(temporary)
            binary = root / "codex"
            binary.write_text("""#!/usr/bin/env python3
import json, sys
sys.stdin.read()
events = [
 {'type':'thread.started','thread_id':'synthetic-build'},
 {'type':'item.completed','item':{'type':'mcp_tool_call','status':'completed','server':'anaxigraph','tool':'inspect','error':{'message':'approval blocked'}}},
 {'type':'item.completed','item':{'type':'mcp_tool_call','status':'completed','server':'anaxigraph','tool':'inspect','result':{'content':[{'type':'text','text':'private implementation evidence'}],'isError':False}}},
 {'type':'turn.completed','usage':{'input_tokens':20,'output_tokens':5,'cached_input_tokens':3}},
]
for event in events:
 print(json.dumps(event), flush=True)
""")
            binary.chmod(0o755)
            progress = {"task": "fixture"}
            snapshots = []
            directory = root / "state"
            directory.mkdir()
            with patch.dict(os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"]}):
                result = run_build.coding(
                    root,
                    directory,
                    "http://127.0.0.1:18782/mcp",
                    "fixture",
                    progress,
                    lambda: snapshots.append(dict(progress)),
                )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["mcp_successes"], 1)
            self.assertEqual(result["mcp_failures"], 1)
            self.assertEqual(result["input_tokens"], 20)
            self.assertEqual(result["output_tokens"], 5)
            self.assertEqual(result["cached_input_tokens"], 3)
            self.assertIsNone(result["cost_usd"])
            self.assertTrue(snapshots)
            self.assertEqual(snapshots[-1]["usage"]["input_tokens"], 20)
            retained = "".join(path.read_text() for path in directory.glob("*.jsonl"))
            self.assertNotIn("private implementation evidence", retained)


class FakeWorkflows:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    async def call(self, tool, arguments):
        self.calls.append((tool, arguments))
        return next(self.replies)


class BuildWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_analysis_rejects_successful_but_partial_scope(self):
        client = FakeWorkflows([{"snapshot": {"id": "stable"}, "modules": 3}])
        partial = {
            "status": "completed",
            "reconciliation": {"status": "completed", "declared_modules": 2},
        }
        with patch.object(run_build, "run_workflow", AsyncMock(return_value=partial)):
            with self.assertRaisesRegex(RuntimeError, "every mapped module"):
                await run_build.analyse(
                    SimpleNamespace(poll_seconds=0), client, {"task": "fixture"}, lambda: None
                )

    async def test_failed_acceptance_gets_a_repair_before_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = SimpleNamespace(repo=root, state=root, app_port=18783)
            entry = {"index": 1, "task": "fixture"}
            client = FakeWorkflows([{}, {}])
            results = [
                {"status": "failed", "passed": 3, "total": 4},
                {"status": "passed", "passed": 4, "total": 4},
            ]
            coder = AsyncMock(return_value={"status": "completed"})
            with (
                patch.object(run_build.run_pair, "verify", side_effect=results),
                patch.object(run_build, "coder", coder),
                patch.object(run_build, "checkpoint", return_value="repaired"),
            ):
                repaired = await run_build.verify_and_repair(
                    config, client, {}, entry, lambda: None
                )
            self.assertTrue(repaired)
            coder.assert_awaited_once()
            self.assertEqual(entry["acceptance"]["status"], "passed")
            self.assertEqual([name for name, _ in client.calls], ["scan", "validate_change"])

    async def test_publication_rejects_partial_patterns_even_with_a_product_review(self):
        client = FakeWorkflows(
            [
                {"snapshot": {"id": "published"}, "annotations": {"current": 2}, "modules": 2},
                {"changed": False},
                {"items": [{"id": "m1"}, {"id": "m2"}], "next_offset": None},
                {"items": [{"id": "p", "status": "current"}], "next_offset": None},
                {
                    "entity": {
                        "data": {
                            "result": {
                                "result": {
                                    "evaluations": [{"module_id": "m1", "pattern_id": "one"}]
                                }
                            }
                        }
                    }
                },
                {"items": [{"id": "f", "status": "current"}], "next_offset": None},
                {"entity": {"data": {"result": {"scope": ["m1", "m2"], "children": ["child"]}}}},
            ]
        )
        entry = {
            "initial_analysis": {
                "workflows": {"patterns": {"reconciliation": {"expected_pairs": 266}}}
            }
        }
        with self.assertRaisesRegex(RuntimeError, "full catalogue and product"):
            await run_build.rebind_publication(None, client, entry, lambda: None)

    async def test_whole_repository_workflow_waits_without_a_deadline(self):
        client = FakeWorkflows(
            [
                {"run_id": "run", "status": "running", "completed_batches": 0, "total_batches": 12},
                {
                    "run_id": "run",
                    "status": "running",
                    "completed_batches": 6,
                    "total_batches": 12,
                    "elapsed_seconds": 100_000,
                },
                {
                    "run_id": "run",
                    "status": "completed",
                    "completed_batches": 12,
                    "total_batches": 12,
                },
            ]
        )
        result = await run_build.run_workflow(client, "mapping", {}, lambda: None, poll_seconds=0)
        self.assertEqual(result["status"], "completed")
        start_tool, start_args = client.calls[0]
        self.assertEqual(start_tool, "workflow_start")
        self.assertEqual(start_args["kind"], "mapping")
        self.assertIsNone(start_args.get("scope"))
        self.assertEqual([tool for tool, _ in client.calls].count("workflow_status"), 2)

    async def test_failed_batches_resume_the_same_run(self):
        failed = {
            "run_id": "run",
            "status": "failed",
            "completed_batches": 11,
            "total_batches": 12,
            "failures": [{"batch_id": 12, "error": "provider unavailable"}],
        }
        client = FakeWorkflows(
            [
                failed,
                {
                    "run_id": "run",
                    "status": "running",
                    "completed_batches": 11,
                    "total_batches": 12,
                },
                {
                    "run_id": "run",
                    "status": "completed",
                    "completed_batches": 12,
                    "total_batches": 12,
                },
            ]
        )
        result = await run_build.run_workflow(client, "patterns", {}, lambda: None, poll_seconds=0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(client.calls[1], ("workflow_resume", {"run_id": "run"}))
        self.assertEqual([tool for tool, _ in client.calls].count("workflow_start"), 1)

    async def test_repeated_genuine_failure_is_not_reported_as_completion(self):
        failed = {
            "run_id": "run",
            "status": "failed",
            "completed_batches": 2,
            "total_batches": 3,
            "failures": [{"batch_id": 3, "error": "invalid response"}],
        }
        client = FakeWorkflows([failed] * 8)
        with self.assertRaises(RuntimeError):
            await run_build.run_workflow(
                client, "fresh-eyes", {}, lambda: None, poll_seconds=0, max_failures=3
            )
        self.assertEqual([tool for tool, _ in client.calls].count("workflow_start"), 1)
        self.assertLessEqual([tool for tool, _ in client.calls].count("workflow_resume"), 3)

    async def test_successful_batch_progress_resets_repeated_failure_counter(self):
        def failed(completed):
            return {
                "run_id": "run",
                "status": "failed",
                "completed_batches": completed,
                "total_batches": 3,
                "failures": [{"batch_id": completed + 1, "error": "provider unavailable"}],
            }

        client = FakeWorkflows(
            [
                failed(1),
                failed(2),
                {
                    "run_id": "run",
                    "status": "completed",
                    "completed_batches": 3,
                    "total_batches": 3,
                },
            ]
        )
        result = await run_build.run_workflow(
            client, "patterns", {}, lambda: None, poll_seconds=0, max_failures=2
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual([tool for tool, _ in client.calls].count("workflow_resume"), 2)


if __name__ == "__main__":
    unittest.main()
