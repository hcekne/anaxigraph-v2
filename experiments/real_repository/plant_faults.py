"""Plant faults in a clone, run the project's whole suite for each, and ask whether the
selection would have run every test file that then fails.

    uv run python experiments/real_repository/plant_faults.py /scratch/clone faults.json \\
        --run 'cd backend && python -m pytest tests -q --junitxml={out}/junit.xml'

`faults.json` is a list of {"name", "path", "old", "new"}, which replaces the one occurrence
of `old`, or {"name", "path", "create"}, which adds a file. The clone needs a saved test
record (`anaxigraph record-tests`). `--run` is the project's own command, started in the
clone; it may write any number of JUnit reports into the folder `{out}`. The suite runs once
without a fault first, to learn which test files fail in this environment anyway.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

os.environ["ANAXIGRAPH_EVIDENCE"] = "off"
from anaxigraph.application import Application

options = argparse.ArgumentParser(description=__doc__.splitlines()[0])
options.add_argument("clone", type=Path)
options.add_argument("faults", type=Path)
options.add_argument("--run", required=True, help="Suite command; {out} is a folder for JUnit")
options.add_argument("--out", type=Path, required=True, help="Folder for reports and results")
args = options.parse_args()
clone = args.clone.resolve()
args.out.mkdir(parents=True, exist_ok=True)
results = args.out / "results.json"
done = json.loads(results.read_text()) if results.exists() else {}


def suite(name: str) -> dict:
    """Run the whole suite, then ask what the selection for the checkout as it is leaves out."""
    folder = (args.out / re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")).resolve()
    folder.mkdir(exist_ok=True)
    subprocess.run(
        args.run.format(out=shlex.quote(str(folder))), shell=True, cwd=clone, check=False
    )
    reports = sorted(folder.glob("*.xml"))
    if not reports:
        raise SystemExit(f"{name}: the run wrote no JUnit report into {folder}")
    [picked] = Application(clone).tests_for_change(full=True, ran=reports)["suites"]
    return picked


def planted(fault: dict) -> dict:
    target = clone / fault["path"]
    original = None if "create" in fault else target.read_text()
    if original is None and target.exists():
        raise SystemExit(f"{fault['name']}: {fault['path']} already exists")
    if original is not None and original.count(fault["old"]) != 1:
        raise SystemExit(f"{fault['name']}: expected the old text exactly once")
    target.write_text(
        fault["create"] if original is None else original.replace(fault["old"], fault["new"])
    )
    try:
        return suite(fault["name"])
    finally:
        target.unlink() if original is None else target.write_text(original)


if "(no fault)" not in done:
    clean = suite("(no fault)")
    # Failing here though not when the record was taken: this environment, not a fault.
    done["(no fault)"] = {"failing_anyway": clean["shadow"]["missed"]}
    results.write_text(json.dumps(done, indent=1))
anyway = set(done["(no fault)"]["failing_anyway"])
print(f"failing without any fault, and not when recorded: {len(anyway)} test files")
for fault in json.loads(args.faults.read_text()):
    if fault["name"] not in done:
        picked = planted(fault)
        shadow = picked["shadow"]
        everything = picked["status"] == "all"
        done[fault["name"]] = {
            "status": picked["status"],
            "selected": picked["selected"],
            "total": picked["total"],
            "time_share": picked["time_share"],
            # Failing now and not when recorded; a few may be the environment's, see above.
            "newly_failing": None if everything else shadow["caught"] + len(shadow["missed"]),
            "missed": sorted(set(shadow["missed"]) - anyway),
        }
        results.write_text(json.dumps(done, indent=1))
    row = done[fault["name"]]
    newly = "all of them run" if row["newly_failing"] is None else row["newly_failing"]
    print(
        f"{fault['name']:44s} {row['status']:9s} {row['selected']:4d} of {row['total']} files"
        f" ({row['time_share']:.0%} of suite time); newly failing files: {newly};"
        f" missed {len(row['missed'])} {row['missed'][:5]}"
    )
