"""Source observations, rather than adopted advice, establish narrow predicates."""

import pytest
from test_extension_points import MEMBER, _closed_repo, _write

from anaxigraph.application import Application


def _boundary_repo(root, *, broken=False):
    _write(
        root,
        {
            "pkg/a.py": ("from pkg.b import run\n" if broken else "")
            + "def public():\n    return 1\ndef _secret():\n    return 2\n",
            "pkg/b.py": "from pkg.a import "
            + ("_secret" if broken else "public")
            + "\ndef run():\n    return "
            + ("_secret" if broken else "public")
            + "()\n",
            "main.py": "from pkg.b import run\nraise RuntimeError('never execute analyzed code')\n",
            "tests/test_main.py": "import main\n",
        },
    )
    app = Application(root)
    app.scan()
    return app


def test_harmful_source_edit_after_assessment_and_composition_root_consumers(tmp_path):
    app = _boundary_repo(tmp_path)
    plan = app.assess_plan("change public", ["pkg/a.py"])
    _write(
        tmp_path,
        {
            "pkg/a.py": "from pkg.b import run\ndef public():\n    return run()\ndef _secret():\n    return 2\n",
            "pkg/b.py": "from pkg.a import _secret\ndef run():\n    return _secret()\n",
            "pkg/other.py": "from pkg.a import public\n",
        },
    )
    app.scan()
    review = app.validate_change()["architecture"]
    assert review["assessed_snapshot_id"] == plan["snapshot_id"]
    findings = review["boundary_changes"]["findings"]
    assert any(
        f["kind"] == "private-interface-access" and f["status"] == "concern" for f in findings
    )
    assert any(f["kind"] == "dependency-cycle" and f["change"] == "introduced" for f in findings)
    assert any(f["kind"] == "shared-consumers" and "pkg/other.py" in f["added"] for f in findings)
    verification = review["independent_verification"]
    assert verification["observed_check_reach"]["examples"] == ["tests/test_main.py"]
    assert verification["status"] == "unresolved"


def test_removed_private_access_and_cycle_are_observed_against_pinned_baseline(tmp_path):
    app = _boundary_repo(tmp_path, broken=True)
    app.assess_plan("fix boundary", ["pkg/a.py", "pkg/b.py"])
    _write(
        tmp_path,
        {
            "pkg/a.py": "def public():\n    return 1\ndef _secret():\n    return 2\n",
            "pkg/b.py": "from pkg.a import public\ndef run():\n    return public()\n",
        },
    )
    app.scan()
    findings = app.validate_change()["architecture"]["boundary_changes"]["findings"]
    fixed = [f for f in findings if f["kind"] in {"private-interface-access", "dependency-cycle"}]
    assert {f["kind"] for f in fixed} == {"private-interface-access", "dependency-cycle"}
    assert all(f["change"] == "improved" and f["status"] == "improved" for f in fixed)


def test_extension_obligation_requires_actual_source_and_does_not_attest_discovery(tmp_path):
    app = _closed_repo(tmp_path)
    files = ["app/providers/geo.py", "app/main.py"]
    [requirement] = app.assess_plan("Add geo", files)["architecture"]["requirements"]
    app.assess_plan(
        "Add geo",
        files,
        [
            {
                "proposal_id": requirement["proposal_id"],
                "decision": "adopt",
                "reason": "Discover members.",
            }
        ],
    )
    [unimplemented] = Application(tmp_path).validate_change()["architecture"]["obligations"]
    assert unimplemented["status"] == "unresolved"
    (tmp_path / "app/providers/geo.py").write_text(MEMBER.format(cls="Geo", name="geo"))
    app.scan()
    [closed] = app.validate_change()["architecture"]["obligations"]
    assert closed["status"] == "violated"
    (tmp_path / "app/main.py").write_text("import pkgutil\nimport app.providers\n")
    app.scan()
    [opened] = app.validate_change()["architecture"]["obligations"]
    assert opened["status"] == "implemented"
    assert opened["proposal_status"] == "unresolved"
    # Moving enumeration to another file cannot make the obligation appear satisfied.
    (tmp_path / "app/wiring.py").write_text(
        "from app.providers.geo import Geo\nfrom app.providers.weather import Weather\n"
    )
    app.scan()
    [moved] = app.validate_change()["architecture"]["obligations"]
    assert moved["status"] == "violated"


def test_unscanned_source_and_parse_failure_cannot_appear_to_fix_boundary(tmp_path):
    app = _boundary_repo(tmp_path, broken=True)
    app.assess_plan("fix boundary", ["pkg/a.py", "pkg/b.py"])
    (tmp_path / "pkg/b.py").write_text("this is invalid Python !!\n")
    with pytest.raises(ValueError, match="scan again"):
        app.validate_change()
    app.scan()
    boundary = app.validate_change()["architecture"]["boundary_changes"]
    assert boundary["unresolved_inputs"]
    assert all(
        f["status"] == "unresolved"
        for f in boundary["findings"]
        if f["change"] == "improved" and f["kind"] != "shared-consumers"
    )
