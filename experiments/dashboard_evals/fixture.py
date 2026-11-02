"""Disposable real HTTP application with explicitly synthetic model answers.

Used only by browser.cjs. No external provider or analyzed project code is run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from anaxigraph.application import Application
from anaxigraph.interfaces.http import create_app
from anaxigraph.workflows.engine import WorkflowRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from test_workflows import answer  # noqa: E402


def fixture(root: Path) -> Application:
    (root / "billing.py").write_text("def invoice():\n    return {'total': 12}\n")
    (root / "client.py").write_text(
        "from billing import invoice\n\ndef total():\n    return invoice()['total']\n"
    )
    application = Application(root)
    application.evidence.arm = "browser-fixture"
    application.evidence.experiment_id = "synthetic-ui-qualification"
    runner = WorkflowRunner(application, lambda request, *_a, **_kw: answer(request))
    application._workflow_runner = runner
    for kind in ("mapping", "patterns", "fresh-eyes"):
        result = runner.start(kind, background=False)
        assert result["status"] == "completed", result
    application.prepare_change("Add an invoice export")
    application.inspect("module:billing.py")
    application.validate_change()
    for number in range(110):
        application.evidence.record(
            "browser_fixture", {"notes": f"Synthetic UI event {number}", "status": "fixture"}
        )
    return application


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if not args.root.is_dir() or any(args.root.iterdir()):
        parser.error("Browser fixture requires an empty disposable directory")
    uvicorn.run(create_app(fixture(args.root)), host="127.0.0.1", port=args.port, log_level="error")


if __name__ == "__main__":
    main()
