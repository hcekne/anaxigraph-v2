"""Release evidence describes validated built bytes and rejects mismatched archives."""

import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def release(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "release_artifacts", Path(__file__).parents[1] / "scripts/release_artifacts.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "source"
    package = source / "src/anaxigraph"
    package.mkdir(parents=True)
    contents = {
        "__init__.py": b'__version__ = "1.0.0"\n',
        "analysis/catalog/patterns.json": b"[]",
        "interfaces/mcp.py": b"def create_mcp(): pass\n",
        "interfaces/cli.py": b"def main(): pass\n",
        **{
            f"interfaces/assets/{name}": b"asset"
            for name in ("index.html", "styles.css", "app.js", "ci.js")
        },
    }
    for name, content in contents.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    project = b'[project]\nname="anaxigraph"\nversion="1.0.0"\nlicense="Apache-2.0"\n'
    for name, data in {
        "pyproject.toml": project,
        "README.md": b"# AnaxiGraph\n",
        "LICENSE": b"Apache License\nVersion 2.0\n",
        "uv.lock": b'[[package]]\nname="anaxigraph"\nversion="1.0.0"\n',
    }.items():
        (source / name).write_bytes(data)
    metadata = (
        b"Metadata-Version: 2.4\nName: anaxigraph\nVersion: 1.0.0\n"
        b"License-Expression: Apache-2.0\nDescription-Content-Type: text/markdown\n"
        b"\n# AnaxiGraph\n"
    )
    wheel = {f"anaxigraph/{name}": content for name, content in contents.items()}
    wheel.update(
        {
            "anaxigraph-1.0.0.dist-info/METADATA": metadata,
            "anaxigraph-1.0.0.dist-info/licenses/LICENSE": (source / "LICENSE").read_bytes(),
            "anaxigraph-1.0.0.dist-info/entry_points.txt": b"[console_scripts]\nanaxigraph = anaxigraph.interfaces.cli:main\n",
        }
    )
    sdist = {f"anaxigraph-1.0.0/src/anaxigraph/{name}": data for name, data in contents.items()}
    sdist.update(
        {
            f"anaxigraph-1.0.0/{name}": (source / name).read_bytes()
            for name in ("LICENSE", "README.md", "pyproject.toml")
        }
    )
    sdist["anaxigraph-1.0.0/PKG-INFO"] = metadata
    dist = tmp_path / "dist"
    dist.mkdir()

    def build():
        with zipfile.ZipFile(dist / "anaxigraph-1.0.0-py3-none-any.whl", "w") as archive:
            for name, data in wheel.items():
                archive.writestr(name, data)
        with tarfile.open(dist / "anaxigraph-1.0.0.tar.gz", "w:gz") as archive:
            for name, data in sdist.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

    def run(commit="a" * 40):
        build()
        module.write_release(dist, commit, tmp_path / "output", root=source)

    return module, source, dist, wheel, sdist, run


def test_manifest_and_checksums_identify_exact_built_distributions(release, tmp_path):
    _, _, dist, _, _, run = release
    run()
    manifest = json.loads((tmp_path / "output/source-manifest.json").read_text())
    expected = [
        {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(dist.iterdir())
    ]
    assert manifest == {
        "version": "1.0.0",
        "source_repository": "hcekne/anaxigraph-v2",
        "source_commit": "a" * 40,
        "artifacts": expected,
    }
    assert (tmp_path / "output/SHA256SUMS").read_text() == "".join(
        f"{item['sha256']}  {item['name']}\n" for item in expected
    )


@pytest.mark.parametrize(
    "fault",
    [
        "version",
        "metadata-version",
        "asset",
        "legacy",
        "sdist-legacy",
        "traversal",
        "readme",
        "commit",
        "ambiguous",
    ],
)
def test_invalid_release_never_writes_evidence(release, tmp_path, fault):
    _, source, dist, wheel, sdist, run = release
    if fault == "version":
        (source / "src/anaxigraph/__init__.py").write_text('__version__ = "0.9.1"\n')
    elif fault == "metadata-version":
        key = "anaxigraph-1.0.0.dist-info/METADATA"
        wheel[key] = wheel[key].replace(b"Version: 1.0.0", b"Version: 0.9.1")
    elif fault == "asset":
        del wheel["anaxigraph/interfaces/assets/styles.css"]
    elif fault == "legacy":
        wheel["anaxigraph/semantic_lease_claim.py"] = b"legacy = True\n"
    elif fault == "sdist-legacy":
        sdist["anaxigraph-1.0.0/src/legacy/__init__.py"] = b"legacy = True\n"
    elif fault == "traversal":
        sdist["../outside"] = b"unsafe"
    elif fault == "readme":
        wheel["anaxigraph-1.0.0.dist-info/METADATA"] += b"stale README\n"
    elif fault == "ambiguous":
        (dist / "anaxigraph-0.9.1-py3-none-any.whl").touch()
    with pytest.raises(ValueError):
        run("not-a-commit" if fault == "commit" else "a" * 40)
    assert not (tmp_path / "output").exists()


def test_archive_links_and_size_limit_are_rejected(release, tmp_path, monkeypatch):
    module, _, _, _, _, _ = release
    archive = tmp_path / "linked.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo("symlink")
        member.type, member.linkname = tarfile.SYMTYPE, "/etc/passwd"
        output.addfile(member)
    with pytest.raises(ValueError, match="Invalid archive member"):
        module.archive_files(archive)
    monkeypatch.setattr(module, "MAX_BYTES", 1)
    with pytest.raises(ValueError, match="Invalid archive"):
        module.archive_files(archive)
