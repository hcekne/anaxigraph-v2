"""Parts, doors and reach-arounds of a small checkout, and what a change adds to them."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import walls  # noqa: E402

FILES = {
    "app/__init__.py": "",
    "app/core/__init__.py": "",
    "app/core/engine.py": (
        "def start():\n    return _tune()\n\n\ndef stop():\n    return 0\n\n\n"
        "def _tune():\n    return 1\n"
    ),
    "app/core/ledger.py": (
        '__all__ = ["shown"]\n\n\ndef shown():\n    return hidden()\n\n\n'
        "def hidden():\n    return 2\n"
    ),
    "app/web/__init__.py": "",
    "app/web/routes.py": (
        "from app.core.engine import _tune, start\nfrom app.core.ledger import hidden\n\n\n"
        "def index():\n    return start() + _tune() + hidden()\n"
    ),
}
ADMIN = "from app.core.engine import _tune, stop\n\n\ndef panel():\n    return stop() + _tune()\n"
TACH = (
    'source_roots = ["."]\n\n[[modules]]\npath = "app.core"\n\n[[modules]]\npath = "app.web"\n\n'
    '[[interfaces]]\nexpose = ["engine\\\\.stop"]\nfrom = ["app.core"]\n'
)


def git(root: Path, *args: str) -> None:
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    subprocess.run(["git", "-C", str(root), *identity, *args], check=True, capture_output=True)


def checkout(root: Path) -> Path:
    for relative, content in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def test_parts_doors_and_reach_arounds(tmp_path, capsys):
    root = checkout(tmp_path / "checkout")
    found = walls.Walls(root, ("app/",))

    assert found.doors() == {
        "app/core/engine.py#start",
        "app/core/engine.py#_tune",
        "app/core/ledger.py#hidden",
    }
    assert {use["door"]: use["why"] for use in found.reach_arounds().values()} == {
        "app/core/engine.py#_tune": "private name",
        "app/core/ledger.py#hidden": "left out of __all__",
    }
    [core] = [row for row in found.parts() if row["part"] == "app/core/"]
    assert core == {
        "part": "app/core/",
        "files": 3,
        "doors": 3,
        "files_with_doors": 2,
        "used_by_parts": 1,
        "reach_arounds": 2,
    }

    # Written down, the same checkout adds nothing; a new file adds a reach-around and a door.
    known = tmp_path / "walls.json"
    assert walls.main([str(root), "--scope", "app", "--write-known", str(known)]) == 0
    assert json.loads(known.read_text())["format"] == walls.FORMAT
    assert walls.main([str(root), "--scope", "app", "--known", str(known)]) == 0
    (root / "app/web/admin.py").write_text(ADMIN)
    capsys.readouterr()
    assert walls.main([str(root), "--scope", "app", "--known", str(known)]) == 1
    printed = capsys.readouterr().out
    assert "New reach-arounds: 1" in printed
    assert "app/web/admin.py:1 uses app/core/engine.py#_tune (private name)" in printed
    assert "New doors: 1" in printed and "app/core/engine.py#stop, first used" in printed


def test_compared_with_an_earlier_revision(tmp_path):
    root = checkout(tmp_path / "checkout")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Start")
    assert walls.main([str(root), "--scope", "app", "--base", "HEAD"]) == 0
    (root / "app/web/admin.py").write_text(ADMIN)
    assert walls.main([str(root), "--scope", "app", "--base", "HEAD"]) == 1


def test_declared_parts_and_interfaces(tmp_path):
    root = checkout(tmp_path / "checkout")
    (root / "app/web/admin.py").write_text(ADMIN)
    (root / "tach.toml").write_text(TACH)
    found = walls.Walls(root, ("app/",), walls.Declared(root / "tach.toml"))

    assert set(found.part.values()) == {"app.core", "app.web", None}  # app/__init__.py is in none.
    assert {use["door"]: use["why"] for use in found.uses.values()} == {
        "app/core/engine.py#start": "not in the declared interface",
        "app/core/engine.py#stop": None,
        "app/core/engine.py#_tune": "private name",
        "app/core/ledger.py#hidden": "left out of __all__",
    }
