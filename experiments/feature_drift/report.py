"""Summarise a feature-drift state directory per arm, and apply the pre-registered rule.

uv run python experiments/feature_drift/report.py --state ~/drift/full [--json out.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean


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


def verification(rows: list[dict], probes: list[dict]) -> dict:
    """Qualification precedes efficiency; absent measurements never imply passed checks."""
    incomplete, failed = [], []
    if sorted(row["step"] for row in rows) != list(range(1, 21)):
        incomplete.append("Expected each step 1..20 exactly once")
    expected_probes = {"p1-geolookup", "p2-notes", "p3-timeouts"}
    if len(probes) != 3 or {row["probe"] for row in probes} != expected_probes:
        incomplete.append("Expected all three probes exactly once")

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
        hidden(row.get("hidden", {}), {f"step_{n:02d}" for n in range(1, row["step"] + 1)}, label)
        suite(row.get("own_suite", {}), label)
        ci = row.get("ci")
        if not ci:
            if row["step"] == 20:
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
    for row in probes:
        label = row["probe"]
        index = label[1:2]
        hidden(row.get("hidden_probe", {}), {f"probe_{index}"}, label)
        hidden(row.get("hidden_regression", {}), {f"step_{n:02d}" for n in range(1, 21)}, label)
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
    meta = json.loads((chain / "chain.json").read_text()) if (chain / "chain.json").exists() else {}
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
                "ci_executing_fraction": ci.get("mean_executing_fraction"),
                "ci_executing_recall": ci.get("executing_recall"),
                "ci_files_selecting_all": ci.get("files_selecting_all"),
                "mutants_caught_by_suite": mutants.get("caught_by_suite"),
                "mutants_planted": mutants.get("planted"),
                "mutants_missed_by_selection": mutants.get("missed_by_selection"),
                "agent_status": (row.get("agent") or {}).get("status"),
                "agent_seconds": (row.get("agent") or {}).get("seconds"),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
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
                "input_tokens": (probe["agent"].get("usage") or {}).get("input_tokens"),
            }
        )
    return {"chain": chain.name, "arm": meta.get("arm", chain.name.rsplit("-r", 1)[0]),
            "replicate": meta.get("replicate"), "verification": verification(rows, probes),
            "steps": steps, "probes": probe_rows}  # fmt: skip


def avg(values):
    values = [v for v in values if v is not None]
    return round(mean(values), 3) if values else None


def arm_summary(chains: list[dict]) -> dict:
    final = [c["steps"][-1] for c in chains if c["steps"]]
    late = [s for c in chains for s in c["steps"] if s["step"] >= 9]
    probes = [p for c in chains for p in c["probes"]]
    mutant_counts = [
        s["mutants_missed_by_selection"] for c in chains for s in c["steps"] if s.get("ci_measured")
    ]
    return {
        "chains": len(chains),
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
        "agent_minutes_total": avg(
            sum(s["agent_seconds"] or 0 for s in c["steps"]) / 60 for c in chains
        ),
        "analysis_minutes_total": avg(
            sum(s["analysis_seconds"] or 0 for s in c["steps"]) / 60 for c in chains
        ),
        "input_tokens_total": avg(sum(s["input_tokens"] or 0 for s in c["steps"]) for c in chains),
        "analysis_model_calls_total": avg(
            sum(s["analysis_model_calls"] or 0 for s in c["steps"]) for c in chains
        ),
    }


def decide(arms: dict, treatment: str = "anaxigraph", control: str = "charter") -> dict:
    """The rule in README.md, fixed before the full run."""
    t, c = arms.get(treatment), arms.get(control)
    if not t or not c or t["last_step"] != 20 or c["last_step"] != 20:
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
    keys = list(next(iter(arms.values())).keys()) if arms else []
    lines = ["| metric | " + " | ".join(arms) + " |", "| --- |" + " ---: |" * len(arms)]
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
            lines.append(
                f"| {s['step']} | {s['new_pass']} | {s['cumulative_rate']:.2f} | {s['regressions']}"
                f" | {s['existing_prod_files_edited']} | {s['prod_loc']} | {s['ci_selected_fraction']}"
                f" | {s['mutants_missed_by_selection']} | {round((s['agent_seconds'] or 0) / 60, 1)} |"
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
    if args.json:
        args.json.write_text(json.dumps(summary, indent=1))
    print(render(summary))


if __name__ == "__main__":
    main()
