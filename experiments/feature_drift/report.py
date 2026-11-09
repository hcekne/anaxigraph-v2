"""Summarise a feature-drift state directory per arm, and apply the pre-registered rule.

uv run python experiments/feature_drift/report.py --state ~/drift/full [--json out.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

try:
    from experiments.feature_drift import scenarios
except ModuleNotFoundError:
    import scenarios


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def regressions(rows: list[dict]) -> dict[int, int]:
    """Per step: hidden tests that passed at their own request's step but fail now."""
    passed_at_intro: set[str] = set()
    result = {}
    for row in sorted(rows, key=lambda r: r["step"]):
        prefix = f"step_{row['step']:02d}"
        failed = set(row["hidden"]["failed"])
        result[row["step"]] = len(passed_at_intro & failed)
        passed_at_intro |= {
            n for n in row["hidden"].get("passed", []) if prefix in n.split("::")[0]
        }
    return result


def verification(rows: list[dict], probes: list[dict], scenario: dict | None = None) -> dict:
    """Qualification precedes efficiency; absent measurements never imply passed checks."""
    scenario = scenario or scenarios.requirements(scenarios.describe())
    incomplete, failed = [], []
    expected_steps = scenario["steps"]
    if scenario["status"] != "executable":
        incomplete.append("Specification-only track: external acceptance checks are not ready")
    if sorted(row["step"] for row in rows) != expected_steps:
        incomplete.append(
            f"Expected each step {expected_steps[0]}..{expected_steps[-1]} exactly once"
        )
    expected_probes = set(scenario["post_history_probes"])
    if len(probes) != len(expected_probes) or {row["probe"] for row in probes} != expected_probes:
        incomplete.append("Expected all post-history probes exactly once")
    for probe in scenario["inline_probes"]:
        matching = [row for row in rows if row["step"] == probe["step"]]
        if len(matching) != 1 or matching[0].get("probe_id") != probe["id"]:
            incomplete.append(f"Missing inline probe {probe['id']} at step {probe['step']}")

    def suite(value, label):
        if not {"returncode", "tests", "passed"} <= value.keys() or not value.get("tests"):
            incomplete.append(label + ": missing suite evidence")
        elif value["returncode"] != 0 or value["passed"] != value["tests"]:
            failed.append(label + ": failed suite")

    def hidden(value, expected, label):
        files = value.get("by_file", {})
        if (
            value.get("collection_error") is not False
            or set(files) != expected
            or any(not row.get("total") or "passed" not in row for row in files.values())
        ):
            incomplete.append(label + ": missing hidden verification")
        if any(row.get("passed") != row.get("total") for row in files.values()):
            failed.append(label + ": failed hidden verification")

    measured = []
    for row in rows:
        label = f"step {row['step']}"
        gate = row.get("ci_gate", {})
        if gate.get("status") == "failed":
            failed.append(label + ": failed external safety/repair qualification")
        elif gate and gate.get("status") != "passed":
            incomplete.append(label + ": external safety repair remains incomplete")
        hidden(
            row.get("hidden", {}),
            {Path(name).stem for name in scenarios.acceptance_files(scenario, row["step"])},
            label,
        )
        suite(row.get("own_suite", {}), label)
        ci = row.get("ci")
        if not ci:
            if row["step"] == expected_steps[-1]:
                incomplete.append("Missing final CI measurement")
            continue
        measured.append(row["step"])
        mutants = ci.get("mutants") or {}
        if not {"planted", "caught_by_suite", "missed_by_selection"} <= mutants.keys() or any(
            type(mutants.get(key)) is not int or mutants[key] < 0
            for key in ("planted", "caught_by_suite", "missed_by_selection")
        ):
            incomplete.append(label + ": missing mutation evidence")
        elif mutants["missed_by_selection"]:
            failed.append(label + ": selected checks missed a required mutant")
        elif not 0 < mutants["caught_by_suite"] <= mutants["planted"]:
            incomplete.append(label + ": no valid caught-mutant obligation")
        coverage = ci.get("coverage_run") or {}
        if (
            ci.get("selector") in {None, "missing"}
            or "selector_failures" not in ci
            or "returncode" not in coverage
        ):
            incomplete.append(label + ": missing CI verification")
        elif ci["selector"] != "ok" or ci["selector_failures"] != 0 or coverage["returncode"] != 0:
            failed.append(label + ": failed CI verification")
        if not ci.get("tests_total") or ci.get("mean_selected_fraction") is None:
            incomplete.append(label + ": missing CI test inventory")
        recall = ci.get("executing_recall")
        if type(recall) not in {int, float} or not 0 <= recall <= 1:
            incomplete.append(label + ": missing observed consumer coverage")
        elif recall < 1:
            incomplete.append(
                label + ": omitted observed consumers; contract substitutions unverified"
            )
    for row in probes:
        label = row["probe"]
        gate = row.get("ci_gate", {})
        if gate.get("status") == "failed":
            failed.append(label + ": failed external safety/repair qualification")
        elif gate and gate.get("status") != "passed":
            incomplete.append(label + ": external safety repair remains incomplete")
        index = scenario["post_history_probes"].index(label) + 1 if label in expected_probes else 0
        hidden(row.get("hidden_probe", {}), {f"probe_{index}"}, label)
        hidden(row.get("hidden_regression", {}), {f"step_{n:02d}" for n in expected_steps}, label)
        suite(row.get("own_suite", {}), label)
        if row.get("ci_selected") is None or not row.get("ci_total"):
            incomplete.append(label + ": missing CI selection")
    return {
        "status": "failed" if failed else "incomplete" if incomplete else "passed",
        "failures": failed,
        "incomplete": incomplete,
        "ci_measured_steps": measured,
    }


def chain_summary(chain: Path) -> dict:
    rows = sorted(read_jsonl(chain / "steps.jsonl"), key=lambda r: r["step"])
    probes = read_jsonl(chain / "probes.jsonl")
    pending = [json.loads(path.read_text()) for path in sorted(chain.glob("pending-step-*.json"))]
    pending_steps = {id(row) for row in pending}
    rows += pending
    rows.sort(key=lambda row: row["step"])
    pending = [json.loads(path.read_text()) for path in sorted(chain.glob("pending-probe-*.json"))]
    pending_probes = {id(row) for row in pending}
    probes += pending
    meta = json.loads((chain / "chain.json").read_text()) if (chain / "chain.json").exists() else {}
    scenario = meta.get("scenario") or scenarios.requirements(scenarios.describe())
    regressed = regressions(rows)
    steps = []
    for row in rows:
        hidden = row["hidden"]["by_file"]
        new = hidden.get(f"step_{row['step']:02d}", {"passed": 0, "total": 0})
        usage = (row.get("agent") or {}).get("usage") or {}
        ci = row.get("ci") or {}
        mutants = ci.get("mutants") or {}
        steps.append(
            {
                "step": row["step"],
                "checkpoint_status": "pending" if id(row) in pending_steps else "accepted",
                "probe_id": row.get("probe_id"),
                "new_pass": f"{new['passed']}/{new['total']}",
                "new_rate": new["passed"] / new["total"] if new["total"] else None,
                "cumulative_rate": sum(v["passed"] for v in hidden.values())
                / max(sum(v["total"] for v in hidden.values()), 1),
                "earlier_failing": sum(
                    v["total"] - v["passed"]
                    for k, v in hidden.items()
                    if k != f"step_{row['step']:02d}"
                ),
                "regressions": regressed[row["step"]],
                "prod_loc": row["static"]["prod_loc"],
                "existing_prod_files_edited": row["diff"]["prod_modified_files"],
                "prod_lines_changed": row["diff"]["prod_lines_added"]
                + row["diff"]["prod_lines_deleted"],
                "duplication": row["static"]["duplication"],
                "import_cycles": row["static"]["import_cycles"],
                "max_complexity": row["static"]["max_complexity"],
                "largest_module_loc": row["static"]["largest_module_loc"],
                "own_tests": row["own_suite"]["tests"],
                "own_suite_seconds": row["own_suite"]["seconds"],
                "ci_measured": bool(ci),
                "ci_selected_fraction": ci.get("mean_selected_fraction"),
                "ci_tests_total": ci.get("tests_total"),
                "ci_mean_selected_tests": ci.get("mean_selected_tests"),
                "ci_selection_completeness": ci.get("selection_completeness"),
                "ci_executing_fraction": ci.get("mean_executing_fraction"),
                "ci_executing_recall": ci.get("executing_recall"),
                "ci_files_selecting_all": ci.get("files_selecting_all"),
                "mutants_caught_by_suite": mutants.get("caught_by_suite"),
                "mutants_planted": mutants.get("planted"),
                "mutants_missed_by_selection": mutants.get("missed_by_selection"),
                "agent_status": (row.get("agent") or {}).get("status"),
                "agent_seconds": (row.get("agent") or {}).get("seconds"),
                "known_agent_seconds": (row.get("agent") or {}).get(
                    "known_seconds", (row.get("agent") or {}).get("seconds") or 0
                ),
                "unknown_agent_seconds": (row.get("agent") or {}).get(
                    "seconds_unknown_sessions", int((row.get("agent") or {}).get("seconds") is None)
                ),
                "continuation": row.get("continuation"),
                "benchmark_access_flag": (row.get("agent") or {}).get("contaminated", False),
                "verification_seconds": row.get("verification_seconds"),
                "coding_sessions": (row.get("agent") or {}).get("sessions", 1),
                "initial_ci_gate": row.get("initial_ci_gate"),
                "final_ci_gate": row.get("ci_gate"),
                "repair_attempts": max(0, len(row.get("ci_attempts", [])) - 1),
                "input_tokens": usage.get("input_tokens"),
                "cached_input_tokens": usage.get("cached_input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "usage_unknown_sessions": (row.get("agent") or {}).get(
                    "usage_unknown_sessions", int(not usage)
                ),
                "known_usage": (row.get("agent") or {}).get("known_usage") or usage,
                "anaxigraph_calls": sum(((row.get("agent") or {}).get("mcp_tools") or {}).values()),
                "analysis_seconds": (row.get("anaxigraph") or {}).get("seconds"),
                "analysis_model_calls": sum(
                    (v or {}).get("model_calls", 0)
                    for k, v in (row.get("anaxigraph") or {}).items()
                    if isinstance(v, dict)
                ),
            }
        )
    probe_rows = []
    for probe in probes:
        own = probe["hidden_probe"]["by_file"]
        reg = probe["hidden_regression"]["by_file"]
        probe_rows.append(
            {
                "probe": probe["probe"],
                "checkpoint_status": "pending" if id(probe) in pending_probes else "accepted",
                "pass": sum(v["passed"] for v in own.values()),
                "total": sum(v["total"] for v in own.values()),
                "regression_rate": sum(v["passed"] for v in reg.values())
                / max(sum(v["total"] for v in reg.values()), 1),
                "existing_prod_files_edited": probe["diff"]["prod_modified_files"],
                "prod_files_added": probe["diff"]["prod_added_files"],
                "prod_lines_changed": probe["diff"]["prod_lines_added"]
                + probe["diff"]["prod_lines_deleted"],
                "ci_selected_fraction": probe["ci_selected"] / probe["ci_total"]
                if probe.get("ci_selected") is not None and probe.get("ci_total")
                else None,
                "agent_seconds": probe["agent"].get("seconds"),
                "known_agent_seconds": probe["agent"].get(
                    "known_seconds", probe["agent"].get("seconds") or 0
                ),
                "unknown_agent_seconds": probe["agent"].get(
                    "seconds_unknown_sessions", int(probe["agent"].get("seconds") is None)
                ),
                "verification_seconds": probe.get("verification_seconds"),
                "initial_ci_gate": probe.get("initial_ci_gate"),
                "final_ci_gate": probe.get("ci_gate"),
                "repair_attempts": max(0, len(probe.get("ci_attempts", [])) - 1),
                "input_tokens": (probe["agent"].get("usage") or {}).get("input_tokens"),
                "cached_input_tokens": (probe["agent"].get("usage") or {}).get(
                    "cached_input_tokens"
                ),
                "output_tokens": (probe["agent"].get("usage") or {}).get("output_tokens"),
                "coding_sessions": probe["agent"].get("sessions", 1),
                "usage_unknown_sessions": probe["agent"].get(
                    "usage_unknown_sessions", int(not probe["agent"].get("usage"))
                ),
                "known_usage": probe["agent"].get("known_usage")
                or probe["agent"].get("usage")
                or {},
            }
        )
    return {"chain": chain.name, "arm": meta.get("arm", chain.name.rsplit("-r", 1)[0]),
            "replicate": meta.get("replicate"), "scenario": scenario,
            "scenario_identity": meta.get("scenario_identity"),
            "verification": verification(rows, probes, scenario),
            "continuation": meta.get("continuation"),
            "steps": steps, "probes": probe_rows}  # fmt: skip


def avg(values):
    values = [v for v in values if v is not None]
    return round(mean(values), 3) if values else None


def mean_total(chains: list[dict], key: str, scale: float = 1) -> float | None:
    totals = []
    for chain in chains:
        rows = [*chain["steps"], *chain["probes"]]
        if any(row.get(key) is None for row in rows):
            return None
        totals.append(sum(row[key] for row in rows) / scale)
    return avg(totals)


def recorded_costs(chains: list[dict]) -> dict:
    """Absolute checkpoint sums; comparison fields retain their existing per-chain means."""
    rows = [row for chain in chains for row in [*chain["steps"], *chain["probes"]]]

    def total(key):
        return (
            sum(row[key] for row in rows) if all(row.get(key) is not None for row in rows) else None
        )

    token_keys = ("input_tokens", "cached_input_tokens", "output_tokens")
    return {
        "scope": "Absolute accepted/pending checkpoint sums; known values are lower bounds. Unfolded session journals are excluded.",
        "accepted_checkpoints": sum(row.get("checkpoint_status") != "pending" for row in rows),
        "pending_checkpoints": sum(row.get("checkpoint_status") == "pending" for row in rows),
        "coding_seconds": total("agent_seconds"),
        "known_coding_seconds": sum(
            row.get("known_agent_seconds", row.get("agent_seconds") or 0) for row in rows
        ),
        "coding_duration_unknown_sessions": sum(
            row.get("unknown_agent_seconds", int(row.get("agent_seconds") is None)) for row in rows
        ),
        "tokens": {key: total(key) for key in token_keys},
        "known_tokens": {
            key: sum((row.get("known_usage") or {}).get(key, row.get(key)) or 0 for row in rows)
            for key in token_keys
        },
        "coding_usage_unknown_sessions": sum(row.get("usage_unknown_sessions", 0) for row in rows),
        "verification_seconds": total("verification_seconds"),
        "known_verification_seconds": sum(row.get("verification_seconds") or 0 for row in rows),
        "verification_unknown_checkpoints": sum(
            row.get("verification_seconds") is None for row in rows
        ),
        "recorded_analysis_seconds": sum(row.get("analysis_seconds") or 0 for row in rows),
    }


def arm_summary(chains: list[dict]) -> dict:
    final = [c["steps"][-1] for c in chains if c["steps"]]
    late = [s for c in chains for s in c["steps"] if s["step"] >= 9]
    probes = [p for c in chains for p in c["probes"]]
    # Tracks with inline probes measure them on the probe requests themselves.
    probes = probes or [
        {
            "pass": int(s["new_pass"].partition("/")[0]),
            "total": int(s["new_pass"].partition("/")[2]),
            "regression_rate": None,
            "existing_prod_files_edited": s["existing_prod_files_edited"],
            "ci_selected_fraction": s["ci_selected_fraction"],
            "agent_seconds": s["agent_seconds"],
        }
        for c in chains
        for s in c["steps"]
        if s.get("probe_id")
    ]
    mutant_counts = [
        s["mutants_missed_by_selection"] for c in chains for s in c["steps"] if s.get("ci_measured")
    ]
    return {
        "chains": len(chains),
        "continuation_chains": [c["chain"] for c in chains if c.get("continuation")],
        "benchmark_access_flags": [
            {"chain": c["chain"], "step": s["step"]}
            for c in chains
            for s in c["steps"]
            if s.get("benchmark_access_flag")
        ],
        "inherited_unqualified_checkpoints": [
            {"chain": c["chain"], "step": s["step"], "gate": s["continuation"]["original_ci_gate"]}
            for c in chains
            for s in c["steps"]
            if s.get("continuation") and s["continuation"].get("original_ci_gate")
        ],
        "scenarios": [
            c.get("scenario") or scenarios.requirements(scenarios.describe()) for c in chains
        ],
        "scenario_identities": [c.get("scenario_identity") for c in chains],
        "replicates": [c.get("replicate") for c in chains],
        "ci_measured_steps": {
            str(c.get("replicate")): c.get("verification", {}).get("ci_measured_steps")
            for c in chains
        },
        "verification_status": "failed"
        if any(c.get("verification", {}).get("status") == "failed" for c in chains)
        else "passed"
        if chains and all(c.get("verification", {}).get("status") == "passed" for c in chains)
        else "incomplete",
        "verification": {c["chain"]: c.get("verification", {}) for c in chains},
        "last_step": max((s["step"] for s in final), default=None),
        "final_cumulative_rate": avg(s["cumulative_rate"] for s in final),
        "mean_new_rate": avg(s["new_rate"] for c in chains for s in c["steps"]),
        "final_earlier_failing": avg(s["earlier_failing"] for s in final),
        "final_regressions": avg(s["regressions"] for s in final),
        "peak_regressions": avg(
            max((s["regressions"] for s in c["steps"]), default=0) for c in chains
        ),
        "final_prod_loc": avg(s["prod_loc"] for s in final),
        "late_existing_files_edited_per_step": avg(s["existing_prod_files_edited"] for s in late),
        "late_prod_lines_changed_per_step": avg(s["prod_lines_changed"] for s in late),
        "final_duplication": avg(s["duplication"] for s in final),
        "final_import_cycles": avg(s["import_cycles"] for s in final),
        "final_max_complexity": avg(s["max_complexity"] for s in final),
        "final_ci_selected_fraction": avg(s["ci_selected_fraction"] for s in final),
        "final_ci_executing_fraction": avg(s["ci_executing_fraction"] for s in final),
        "final_ci_executing_recall": avg(s["ci_executing_recall"] for s in final),
        "mutants_missed_by_selection": sum(mutant_counts)
        if mutant_counts and all(type(value) is int for value in mutant_counts)
        else None,
        "final_own_suite_seconds": avg(s["own_suite_seconds"] for s in final),
        "probe_pass": f"{sum(p['pass'] for p in probes)}/{sum(p['total'] for p in probes)}"
        if probes
        else None,
        "probe_regression_rate": avg(p["regression_rate"] for p in probes),
        "probe_existing_files_edited": avg(p["existing_prod_files_edited"] for p in probes),
        "probe_ci_selected_fraction": avg(p["ci_selected_fraction"] for p in probes),
        "probe_agent_seconds": avg(p["agent_seconds"] for p in probes),
        "comparison_cost_semantics": "Timing/token *_total fields are per-chain means, not arm spend; session counts are sums.",
        "agent_minutes_total": mean_total(chains, "agent_seconds", 60),
        "recorded_costs": recorded_costs(chains),
        "known_agent_minutes_total": avg(
            sum(s.get("known_agent_seconds", s.get("agent_seconds") or 0) for s in c["steps"]) / 60
            for c in chains
        ),
        "agent_duration_unknown_sessions": sum(
            s.get("unknown_agent_seconds", 0) for c in chains for s in c["steps"]
        ),
        "analysis_minutes_total": avg(
            sum(s["analysis_seconds"] or 0 for s in c["steps"]) / 60 for c in chains
        ),
        "verification_minutes_total": mean_total(chains, "verification_seconds", 60),
        "repair_sessions_total": sum(
            s["repair_attempts"] for c in chains for s in [*c["steps"], *c["probes"]]
        ),
        "input_tokens_total": mean_total(chains, "input_tokens"),
        "cached_input_tokens_total": mean_total(chains, "cached_input_tokens"),
        "output_tokens_total": mean_total(chains, "output_tokens"),
        "coding_sessions_total": sum(
            s["coding_sessions"] for c in chains for s in [*c["steps"], *c["probes"]]
        ),
        "coding_usage_unknown_sessions": sum(
            s["usage_unknown_sessions"] for c in chains for s in [*c["steps"], *c["probes"]]
        ),
        "analysis_model_calls_total": avg(
            sum(s["analysis_model_calls"] or 0 for s in c["steps"]) for c in chains
        ),
    }


def decide(arms: dict, treatment: str = "anaxigraph", control: str = "charter") -> dict:
    """The rule in README.md, fixed before the full run."""
    t, c = arms.get(treatment), arms.get(control)
    if not t or not c:
        return {"verdict": "incomplete"}
    if t.get("continuation_chains") or c.get("continuation_chains"):
        return {
            "verdict": "exploratory-continuation",
            "reason": "Extra repairs and inherited histories are explicitly outside the original preregistered comparison; inspect qualification and descriptive measurements.",
        }
    definitions = t.get("scenarios", []) + c.get("scenarios", [])
    scenario = definitions[0] if definitions else scenarios.requirements(scenarios.describe())
    identities = t.get("scenario_identities", []) + c.get("scenario_identities", [])
    if (
        scenario.get("status") != "executable"
        or scenario.get("comparison_rule") not in scenarios.COMPARISON_RULES.values()
        or any(value != scenario for value in definitions)
        or identities
        and any(value != identities[0] for value in identities)
    ):
        return {
            "verdict": "incomplete",
            "reason": "Need one executable scenario and matching frozen identities with a registered comparison rule",
        }
    if t["last_step"] != scenario["steps"][-1] or c["last_step"] != scenario["steps"][-1]:
        return {"verdict": "incomplete"}

    tr, cr = t.get("replicates", []), c.get("replicates", [])
    matched = (
        len(tr) >= 3
        and len(cr) >= 3
        and all(type(n) is int for n in tr + cr)
        and len(set(tr)) == len(tr)
        and len(set(cr)) == len(cr)
        and set(tr) == set(cr)
    )
    cadence_matches = matched and all(
        t.get("ci_measured_steps", {}).get(str(n)) == c.get("ci_measured_steps", {}).get(str(n))
        and t.get("ci_measured_steps", {}).get(str(n))
        for n in tr
    )
    if not cadence_matches or any(
        arm.get("verification_status") not in {"passed", "failed"} for arm in (t, c)
    ):
        return {
            "verdict": "incomplete",
            "reason": "Need at least three matched complete verified chains per arm",
        }
    safety = all(
        arm.get("verification_status") == "passed" and arm.get("mutants_missed_by_selection") == 0
        for arm in (t, c)
    )

    def lower_by(key, share=0.2):
        a, b = t.get(key), c.get(key)
        return a is not None and b is not None and b > 0 and a <= b * (1 - share)

    def parse(text):
        passed, _, total = (text or "0/0").partition("/")
        return int(passed), int(total or 0)

    tp, cp = parse(t["probe_pass"]), parse(c["probe_pass"])
    criteria = {
        "safety_qualified": safety,
        "behaviour_not_worse": t["final_cumulative_rate"] >= c["final_cumulative_rate"] - 0.02,
        "less_change_amplification": lower_by("late_existing_files_edited_per_step"),
        "easier_to_change": tp[0] > cp[0]
        or (tp[0] == cp[0] and lower_by("probe_existing_files_edited")),
        "cheaper_safe_ci": safety and lower_by("final_ci_selected_fraction"),
    }
    wins = sum(
        criteria[k] for k in ("less_change_amplification", "easier_to_change", "cheaper_safe_ci")
    )
    verdict = (
        "treatment better"
        if safety and criteria["behaviour_not_worse"] and wins >= 2
        else "not shown"
    )
    coding, analysis, base = (
        t.get("agent_minutes_total"),
        t.get("analysis_minutes_total"),
        c.get("agent_minutes_total"),
    )
    cost = coding + analysis if coding is not None and analysis is not None else None
    return {
        "verdict": verdict,
        "criteria": criteria,
        "time_ratio": round(cost / base, 2) if cost is not None and base else None,
        "within_cost_budget": cost <= 1.5 * base if cost is not None and base else None,
    }


def render(summary: dict) -> str:
    arms = summary["arms"]
    keys = (
        [
            key
            for key in next(iter(arms.values()))
            if key not in {"scenarios", "scenario_identities"}
        ]
        if arms
        else []
    )
    lines = [
        "Timing and token fields ending `_total` retain per-chain comparison means, not arm spend.",
        "`recorded_costs` sums checkpoint rows; known values are lower bounds when receipts are missing.",
        "Completed session journals not yet folded into checkpoints require the separate cost audit.",
        "",
        "| metric | " + " | ".join(arms) + " |",
        "| --- |" + " ---: |" * len(arms),
    ]
    for key in keys:
        lines.append(f"| {key} | " + " | ".join(str(arms[a][key]) for a in arms) + " |")
    lines.append("")
    lines.append(f"Decision (anaxigraph vs charter): {json.dumps(summary['decision'])}")
    lines.append("")
    for chain in summary["chains"]:
        lines.append(f"### {chain['chain']}")
        lines.append(
            "| step | new | cumulative | regressions | edited files | LOC | CI selected | missed bugs | agent min |"
        )
        lines.append("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for s in chain["steps"]:
            minutes = (
                round(s["agent_seconds"] / 60, 1) if s["agent_seconds"] is not None else "unknown"
            )
            lines.append(
                f"| {s['step']} | {s['new_pass']} | {s['cumulative_rate']:.2f} | {s['regressions']}"
                f" | {s['existing_prod_files_edited']} | {s['prod_loc']} | {s['ci_selected_fraction']}"
                f" | {s['mutants_missed_by_selection']} | {minutes} |"
            )
        for p in chain["probes"]:
            lines.append(
                f"- probe {p['probe']}: {p['pass']}/{p['total']}, edited {p['existing_prod_files_edited']} existing files, CI selected {p['ci_selected_fraction']}"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    chains = [
        chain_summary(path)
        for path in sorted(args.state.iterdir())
        if path.is_dir() and (path / "steps.jsonl").exists()
    ]
    by_arm: dict[str, list[dict]] = {}
    for chain in chains:
        by_arm.setdefault(chain["arm"], []).append(chain)
    arms = {arm: arm_summary(rows) for arm, rows in by_arm.items()}
    summary = {"arms": arms, "decision": decide(arms), "chains": chains}
    manifest = args.state / "continuation.json"
    if manifest.exists():
        value = json.loads(manifest.read_text())
        summary["continuation"] = {
            key: value[key]
            for key in (
                "protocol",
                "source_state",
                "created",
                "additional_ci_repairs",
                "note",
                "audited_contamination",
            )
            if key in value
        }
    if args.json:
        args.json.write_text(json.dumps(summary, indent=1))
    print(render(summary))


if __name__ == "__main__":
    main()
