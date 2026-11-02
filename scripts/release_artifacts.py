"""Validate built distributions and record their immutable release identities."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import stat
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 64 * 1024 * 1024


def archive_files(path: Path) -> dict[str, bytes]:
    """Read bounded regular members without extracting untrusted archive paths."""
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError(f"Invalid archive: {path.name}")
    files: dict[str, bytes] = {}
    total = 0
    with zipfile.ZipFile(path) if path.suffix == ".whl" else tarfile.open(path) as archive:
        members = archive.infolist() if isinstance(archive, zipfile.ZipFile) else archive
        for count, member in enumerate(members, start=1):
            if count > 10_000:
                raise ValueError("Too many archive members")
            name = member.filename if isinstance(member, zipfile.ZipInfo) else member.name
            if (
                not name
                or name.startswith("/")
                or "\\" in name
                or any(part in {"..", "."} for part in name.split("/"))
            ):
                raise ValueError(f"Unsafe archive path: {name}")
            if member.is_dir() if isinstance(member, zipfile.ZipInfo) else member.isdir():
                continue
            regular = (
                stat.S_IFMT(member.external_attr >> 16) in {0, stat.S_IFREG}
                if isinstance(member, zipfile.ZipInfo)
                else member.isfile()
            )
            size = member.file_size if isinstance(member, zipfile.ZipInfo) else member.size
            total += size
            if not regular or name in files or total > MAX_BYTES:
                raise ValueError(f"Invalid archive member: {name}")
            stream = (
                archive.open(member)
                if isinstance(archive, zipfile.ZipFile)
                else archive.extractfile(member)
            )
            if stream is None:
                raise ValueError(f"Unreadable archive member: {name}")
            with stream:
                files[name] = stream.read(MAX_BYTES + 1)
            if len(files[name]) != size:
                raise ValueError(f"Invalid archive member size: {name}")
    return files


def runtime_version(content: bytes) -> str:
    for node in ast.parse(content).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError("Missing runtime version")


def write_release(dist: Path, source_commit: str, output: Path, *, root: Path = ROOT) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("Source commit must be a full lowercase 40-character SHA")
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    version = project["version"]
    if (
        project["name"] != "anaxigraph"
        or project["license"] != "Apache-2.0"
        or not re.fullmatch(r"\d+\.\d+\.\d+", version)
    ):
        raise ValueError("Invalid project name, version or license")
    package = root / "src" / "anaxigraph"
    expected = {
        path.relative_to(package).as_posix(): path.read_bytes()
        for path in package.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    required = {
        "__init__.py",
        "analysis/catalog/patterns.json",
        "interfaces/mcp.py",
        "interfaces/cli.py",
        "interfaces/assets/index.html",
        "interfaces/assets/styles.css",
        "interfaces/assets/app.js",
        "interfaces/assets/ci.js",
    }
    locked = tomllib.loads((root / "uv.lock").read_text())["package"]
    if (
        not required <= expected.keys()
        or runtime_version(expected["__init__.py"]) != version
        or [p["version"] for p in locked if p["name"] == "anaxigraph"] != [version]
    ):
        raise ValueError("Source package assets or versions disagree")
    wheels, sdists = sorted(dist.glob("*.whl")), sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Expected exactly one wheel and one sdist")
    wheel, sdist = wheels[0], sdists[0]
    prefix = f"anaxigraph-{version}"
    if not wheel.name.startswith(prefix + "-") or sdist.name != prefix + ".tar.gz":
        raise ValueError("Distribution filenames disagree with source version")
    wheel_files, sdist_files = archive_files(wheel), archive_files(sdist)
    metadata_dir = f"anaxigraph-{version}.dist-info"
    for name in wheel_files:
        if PurePosixPath(name).parts[0] not in {"anaxigraph", metadata_dir}:
            raise ValueError(f"Unexpected wheel content: {name}")
    for name in sdist_files:
        if not name.startswith(prefix + "/") or (
            name.startswith(f"{prefix}/src/")
            and not name.startswith(
                (f"{prefix}/src/anaxigraph/", f"{prefix}/src/anaxigraph.egg-info/")
            )
        ):
            raise ValueError(f"Unexpected sdist content: {name}")
    for files, base, metadata in (
        (wheel_files, "anaxigraph/", f"{metadata_dir}/METADATA"),
        (sdist_files, f"{prefix}/src/anaxigraph/", f"{prefix}/PKG-INFO"),
    ):
        actual = {
            name.removeprefix(base): data for name, data in files.items() if name.startswith(base)
        }
        if actual != expected:
            raise ValueError(
                "Distribution package differs from source (missing assets or legacy runtime)"
            )
        record = BytesParser().parsebytes(files[metadata])
        if (
            record["Name"] != "anaxigraph"
            or record["Version"] != version
            or record["License-Expression"] != "Apache-2.0"
            or record["Description-Content-Type"] != "text/markdown"
            or record.get_payload(decode=True).decode("utf-8").strip()
            != (root / "README.md").read_text().strip()
        ):
            raise ValueError("Distribution metadata or README differs from source")
    if (
        wheel_files[f"{metadata_dir}/licenses/LICENSE"] != (root / "LICENSE").read_bytes()
        or sdist_files[f"{prefix}/LICENSE"] != (root / "LICENSE").read_bytes()
        or sdist_files[f"{prefix}/README.md"] != (root / "README.md").read_bytes()
        or tomllib.loads(sdist_files[f"{prefix}/pyproject.toml"].decode())["project"] != project
    ):
        raise ValueError("Distribution source metadata or license differs from source")
    entrypoints = wheel_files[f"{metadata_dir}/entry_points.txt"].decode()
    if "anaxigraph = anaxigraph.interfaces.cli:main" not in entrypoints:
        raise ValueError("Missing CLI/MCP entry point")
    artifacts = [
        {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in (wheel, sdist)
    ]
    manifest = {
        "version": version,
        "source_repository": "hcekne/anaxigraph-v2",
        "source_commit": source_commit,
        "artifacts": artifacts,
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, content in {
        "source-manifest.json": json.dumps(manifest, indent=2) + "\n",
        "SHA256SUMS": "".join(f"{item['sha256']}  {item['name']}\n" for item in artifacts),
    }.items():
        with tempfile.NamedTemporaryFile(mode="w", dir=output, delete=False) as temporary:
            temporary.write(content)
        Path(temporary.name).replace(output / name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        write_release(args.dist, args.source_commit, args.output)
    except (ValueError, KeyError, OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Release validation failed: {error}\n")


if __name__ == "__main__":
    main()
