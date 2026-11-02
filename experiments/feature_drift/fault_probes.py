"""No-model sampled faults in disposable generic fixtures, never historical trial edits.

Run with --selection local to expose omitted consumer assertions (exit 1), or widened
for all fixture contracts. Both reports include the independent reference comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CACHE = """class Cache:
    def __init__(self):
        self.values = {}

    def get(self, tenant, credential, resource, load):
        key = (tenant, credential, resource)
        if key not in self.values:
            self.values[key] = load()
        return self.values[key]

    def invalidate(self, tenant):
        self.values = {key: value for key, value in self.values.items() if key[0] != tenant}
"""

BREAKER = """class Breaker:
    def __init__(self, threshold=2, cooldown=5):
        self.threshold, self.cooldown = threshold, cooldown
        self.failures, self.opened = {}, {}

    def key(self, tenant, upstream):
        return (tenant, upstream)

    def allowed(self, tenant, upstream, now):
        key = self.key(tenant, upstream)
        if key not in self.opened:
            return True
        return now >= self.opened[key] + self.cooldown

    def fail(self, tenant, upstream, now):
        key = self.key(tenant, upstream)
        self.failures[key] = self.failures.get(key, 0) + 1
        if self.failures[key] >= self.threshold:
            self.opened[key] = now
"""

LOCAL = """import unittest
from cache import Cache
from breaker import Breaker

class LocalTests(unittest.TestCase):
    def test_cache_reuses_a_value(self):
        cache = Cache()
        self.assertEqual(cache.get("tenant", "token", "items", lambda: 1), 1)
        self.assertEqual(cache.get("tenant", "token", "items", lambda: 2), 1)

    def test_one_failure_is_below_threshold(self):
        breaker = Breaker()
        breaker.fail("tenant", "upstream", 0)
        self.assertTrue(breaker.allowed("tenant", "upstream", 1))
"""

CONSUMERS = """import unittest
from cache import Cache
from breaker import Breaker

class ConsumerContracts(unittest.TestCase):
    def test_tenants_cannot_share_cached_results(self):
        cache = Cache()
        cache.get("a", "same-token", "items", lambda: "a-private")
        self.assertEqual(cache.get("b", "same-token", "items", lambda: "b-private"), "b-private")

    def test_rotated_credentials_cannot_reuse_old_authorization(self):
        cache = Cache()
        cache.get("a", "old-token", "items", lambda: "old-permissions")
        self.assertEqual(cache.get("a", "new-token", "items", lambda: "new-permissions"), "new-permissions")

    def test_mutation_invalidation_refreshes_the_consumer(self):
        cache = Cache()
        cache.get("a", "token", "items", lambda: "before-mutation")
        cache.invalidate("a")
        self.assertEqual(cache.get("a", "token", "items", lambda: "after-mutation"), "after-mutation")

    def opened_breaker(self):
        breaker = Breaker(threshold=2, cooldown=5)
        breaker.fail("a", "upstream", 0)
        breaker.fail("a", "upstream", 1)
        return breaker

    def test_threshold_blocks_the_next_consumer_call(self):
        self.assertFalse(self.opened_breaker().allowed("a", "upstream", 2))

    def test_cooldown_reopens_at_the_declared_boundary(self):
        breaker = self.opened_breaker()
        self.assertFalse(breaker.allowed("a", "upstream", 5))
        self.assertTrue(breaker.allowed("a", "upstream", 6))

    def test_failed_tenant_cannot_block_another_tenant(self):
        self.assertTrue(self.opened_breaker().allowed("b", "upstream", 2))

    def test_failed_upstream_cannot_block_another_upstream(self):
        self.assertTrue(self.opened_breaker().allowed("a", "other-upstream", 2))
"""

FILES = {
    "cache.py": CACHE,
    "breaker.py": BREAKER,
    "test_local.py": LOCAL,
    "test_consumers.py": CONSUMERS,
}
FAULTS = (
    (
        "cache_tenant_isolation",
        "cache.py",
        "key = (tenant, credential, resource)",
        "key = (None, credential, resource)",
    ),
    (
        "cache_credential_rotation",
        "cache.py",
        "key = (tenant, credential, resource)",
        "key = (tenant, None, resource)",
    ),
    (
        "cache_invalidation",
        "cache.py",
        "self.values = {key: value for key, value in self.values.items() if key[0] != tenant}",
        "pass",
    ),
    (
        "breaker_threshold",
        "breaker.py",
        "self.failures[key] >= self.threshold",
        "self.failures[key] > self.threshold",
    ),
    (
        "breaker_cooldown",
        "breaker.py",
        "now >= self.opened[key] + self.cooldown",
        "now > self.opened[key] + self.cooldown",
    ),
    ("breaker_tenant_scope", "breaker.py", "return (tenant, upstream)", "return (None, upstream)"),
    ("breaker_upstream_scope", "breaker.py", "return (tenant, upstream)", "return (tenant, None)"),
)
LOCAL_SCOPE = ["test_local"]
REFERENCE_SCOPE = ["test_local", "test_consumers"]


def _run(root: Path, scope: list[str]) -> dict:
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "-v", *scope],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    count = re.search(r"Ran (\d+) tests?", completed.stderr)
    if completed.returncode not in {0, 1} or not count:
        raise RuntimeError(f"Fixture runner failed: {completed.stderr[:2000]}")
    return {
        "exit_code": completed.returncode,
        "tests": int(count.group(1)),
        "seconds": round(time.monotonic() - started, 6),
        "failed_tests": re.findall(r"^FAIL: (.+)$", completed.stderr, re.MULTILINE),
    }


def qualify(selection: str = "local") -> dict:
    if selection not in {"local", "widened"}:
        raise ValueError("selection must be local or widened")
    started = time.monotonic()
    selected_scope = LOCAL_SCOPE if selection == "local" else REFERENCE_SCOPE
    rows = []
    with tempfile.TemporaryDirectory(prefix="anaxigraph-fault-probes-") as temporary:
        root = Path(temporary)
        for path, source in FILES.items():
            (root / path).write_text(source)
        baseline = _run(root, REFERENCE_SCOPE)
        if baseline["exit_code"]:
            raise RuntimeError(f"Baseline fixture does not pass: {baseline}")
        for name, path, before, after in FAULTS:
            source = FILES[path]
            if source.count(before) != 1:
                raise ValueError(f"Fault {name} must change exactly one source location")
            mutated = source.replace(before, after)
            (root / path).write_text(mutated)
            reference = _run(root, REFERENCE_SCOPE)
            selected = _run(root, selected_scope)
            widened = _run(root, REFERENCE_SCOPE)
            (root / path).write_text(source)
            rows.append(
                {
                    "name": name,
                    "path": path,
                    "source_sha256": hashlib.sha256(mutated.encode()).hexdigest(),
                    "reference_detected": reference["exit_code"] != 0,
                    "selected_detected": selected["exit_code"] != 0,
                    "widened_detected": widened["exit_code"] != 0,
                    "reference": reference,
                    "selected": selected,
                    "widened": widened,
                }
            )
    detected = [row for row in rows if row["reference_detected"]]
    missed = [row["name"] for row in detected if not row["selected_detected"]]
    return {
        "schema": "anaxigraph.synthetic-fault-qualification.v1",
        "basis": "Disposable generic fixtures and sampled injected faults; no historical escaped-bug claim.",
        "selection": selection,
        "selected_scope": selected_scope,
        "reference_scope": REFERENCE_SCOPE,
        "baseline": baseline,
        "baseline_sources": {p: hashlib.sha256(s.encode()).hexdigest() for p, s in FILES.items()},
        "faults": rows,
        "counts": {
            "injected": len(rows),
            "reference_detected": len(detected),
            "selected_detected": sum(row["selected_detected"] for row in detected),
            "widened_detected": sum(row["widened_detected"] for row in detected),
            "selection_misses": len(missed),
        },
        "missed_faults": missed,
        "status": "unsafe_selection" if missed else "sampled_faults_detected",
        "seconds": round(time.monotonic() - started, 6),
        "provider_calls": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", choices=("local", "widened"), default="local")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = qualify(args.selection)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "counts": result["counts"],
                "seconds": result["seconds"],
                "output": str(args.output),
            }
        )
    )
    return int(result["status"] == "unsafe_selection")


if __name__ == "__main__":
    raise SystemExit(main())
