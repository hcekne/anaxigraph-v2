"""Harness checks are separate from the product's runtime test suite."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import acceptance
import trial

HERE = Path(__file__).parent


class EvidenceTests(unittest.TestCase):
    def test_cli_record_export_compare_retains_unknowns(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "call.json"
            payload.write_text(
                json.dumps(
                    {
                        "model": "gpt-6.1-sol",
                        "reasoning_effort": "xhigh",
                        "input_tokens": 120,
                        "output_tokens": None,
                        "usage_basis": "provider_reported",
                        "cost_usd": None,
                    }
                )
            )
            command = [
                sys.executable,
                str(HERE / "trial.py"),
                "--database",
                str(root / "trial.sqlite"),
            ]
            result = subprocess.run(
                command
                + [
                    "record",
                    "--arm",
                    "control",
                    "--task",
                    "task1",
                    "--kind",
                    "coding_call",
                    "--payload",
                    str(payload),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(json.loads(result.stdout)["event_id"])
            exported = subprocess.run(
                command + ["export"], check=True, capture_output=True, text=True
            )
            self.assertEqual(len(json.loads(exported.stdout)["events"]), 1)
            output = root / "comparison.json"
            subprocess.run(
                command + ["compare", "--output", str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            comparison = json.loads(output.read_text())["arms"]["control"]["coding"]
            self.assertEqual(comparison["input_tokens"]["reported_sum"], 120)
            self.assertIsNone(comparison["cost_usd"]["reported_sum"])
            self.assertEqual(comparison["cost_usd"]["unknown_events"], 1)
            self.assertFalse(comparison["output_tokens"]["complete"])

    def test_capacity_preserves_prior_event_and_unknown_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.sqlite"
            with patch.object(trial, "MAX_EVENTS", 1):
                trial.record(path, "treatment", "task1", "task_started", {"task": "Build the app"})
                with self.assertRaisesRegex(ValueError, "budget"):
                    trial.record(path, "treatment", "task2", "task_started", {})
            self.assertEqual(len(trial.events(path)), 1)
            with self.assertRaisesRegex(ValueError, "unknown fields"):
                trial.record(path, "control", "task1", "review", {"raw_source": "not metrics"})
            with self.assertRaisesRegex(ValueError, "basis"):
                trial.record(path, "control", "task1", "coding_call", {"cost_usd": 5})


class AcceptanceTests(unittest.TestCase):
    def test_real_http_checks_detect_a_missing_trip_contract(self):
        resorts = json.loads((HERE / "seed.json").read_text())["resorts"]

        class Fixture(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/":
                    body, content_type = b"<h1>Snow Rankings</h1>", "text/html"
                elif self.path == "/api/status":
                    body, content_type = (
                        json.dumps({"status": "ok", "data_mode": "demo", "resorts": 6}).encode(),
                        "application/json",
                    )
                elif self.path.startswith("/api/resorts?"):
                    selected = resorts[:2] if "limit=2&" in self.path else resorts
                    body, content_type = (
                        json.dumps({"items": selected, "total": 6}).encode(),
                        "application/json",
                    )
                elif self.path == "/api/resorts/does-not-exist":
                    self.send_error(404)
                    return
                elif self.path.startswith("/api/resorts/"):
                    body, content_type = (
                        b'{"forecast":[],"sources":[{"name":"synthetic"}]}',
                        "application/json",
                    )
                else:
                    body, content_type = (
                        b'{"items":[{"resort":{"id":"broken"}}],"total":1}',
                        "application/json",
                    )
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = acceptance.evaluate(f"http://127.0.0.1:{server.server_port}", milestone=1)
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
        self.assertEqual(result["passed"], 3)
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["checks"][-1]["status"], "failed")

    def test_nonfinite_and_boolean_values_are_not_measurements(self):
        self.assertFalse(acceptance.finite(float("nan")))
        self.assertFalse(acceptance.finite(True))
        self.assertTrue(acceptance.finite(0))


if __name__ == "__main__":
    unittest.main()
