"""Evaluator faults cannot survive a killed worker or become attributed to paid coding."""

import json
import signal
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutation_state as state  # noqa: E402


@pytest.fixture
def source(tmp_path):
    file = tmp_path / "logic.py"
    file.write_bytes(b"def choose(value):\r\n    return value + 1\r\n")
    file.chmod(0o750)
    return tmp_path, file


def test_temporary_mutation_restores_exact_bytes_and_mode(source):
    root, file = source
    original = file.read_bytes()
    with state.injected(root, file, "broken = True", {"kind": "fixture"}):
        assert file.read_text() == "broken = True"
        assert state.journal(root).exists()
        with pytest.raises(ValueError, match="still owns"):
            state.restore(root)
    assert file.read_bytes() == original
    assert stat.S_IMODE(file.stat().st_mode) == 0o750
    assert not state.journal(root).exists()


def test_exception_also_restores_source(source):
    root, file = source
    original = file.read_bytes()
    with pytest.raises(RuntimeError, match="verification interrupted"):
        with state.injected(root, file, "broken = True", {}):
            raise RuntimeError("verification interrupted")
    assert file.read_bytes() == original
    assert not state.journal(root).exists()


def test_killed_evaluator_can_be_recovered_from_durable_journal(source):
    root, file = source
    original = file.read_bytes()
    script = (
        "import os,signal,sys; from pathlib import Path; "
        f"sys.path.insert(0, {str(Path(state.__file__).parent)!r}); "
        "import mutation_state as state; "
        f"root=Path({str(root)!r}); "
        "context=state.injected(root, root/'logic.py', 'broken = True', {'kind':'fixture'}); "
        "context.__enter__(); os.kill(os.getpid(), signal.SIGKILL)"
    )
    result = subprocess.run([sys.executable, "-c", script])
    assert result.returncode == -signal.SIGKILL
    assert file.read_text() == "broken = True"
    observed = state.restore(root)
    assert observed["original_sha256"] == state.digest(original)
    assert observed["restoration"] == "interrupted-evaluator-recovery"
    assert file.read_bytes() == original and not state.journal(root).exists()


def test_journal_never_overwrites_independent_source_changes(source):
    root, file = source
    with pytest.raises(ValueError, match="changed outside"):
        with state.injected(root, file, "broken = True", {}):
            file.write_text("independent = True\n")
    assert file.read_text() == "independent = True\n"
    assert state.journal(root).exists()


def test_journal_rejects_path_traversal_before_restoring(source):
    root, file = source
    with pytest.raises(ValueError, match="inside the candidate"):
        with state.injected(root, file, "broken = True", {}):
            record = json.loads(state.journal(root).read_text())
            record["path"] = "../outside.py"
            state.journal(root).write_text(json.dumps(record))
    assert not (root.parent / "outside.py").exists()


def test_journal_verifies_its_original_backup(source):
    root, file = source
    with pytest.raises(ValueError, match="does not match"):
        with state.injected(root, file, "broken = True", {}):
            record = json.loads(state.journal(root).read_text())
            record["original_sha256"] = "wrong"
            state.journal(root).write_text(json.dumps(record))
    assert file.read_text() == "broken = True"
    assert state.journal(root).exists()


def test_faults_are_not_injected_when_the_reference_suite_already_fails(tmp_path, monkeypatch):
    import metrics

    (tmp_path / "ci.json").write_text("{}")
    tests = ["tests/test_logic.py::test_logic"]
    monkeypatch.setattr(metrics, "collect", lambda *args: tests)
    monkeypatch.setattr(metrics, "production_files", lambda *args: ["logic.py"])
    monkeypatch.setattr(metrics, "select", lambda *args: (["ALL"], 0))
    monkeypatch.setattr(
        metrics,
        "coverage_by_test",
        lambda *args: ({"logic.py": {1: set(tests)}}, {"returncode": 1}),
    )
    monkeypatch.setattr(
        metrics,
        "plant_bugs",
        lambda *args, **kwargs: pytest.fail("Mutated an already failing baseline"),
    )
    result = metrics.ci_quality(tmp_path, 1)
    assert result["mutants"]["planted"] == 0
    assert result["coverage_run"]["returncode"] == 1
