"""Crash-safe ownership of temporary evaluator mutations, outside candidate source inputs."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

JOURNAL = "drift-active-mutation.json"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic(path: Path, value: bytes, mode=0o600) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), mode)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def owner() -> dict:
    value = {"pid": os.getpid()}
    try:
        value["process_start"] = (
            Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()[19]
        )
        value["boot_id"] = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        pass
    return value


def alive(value: dict) -> bool:
    try:
        os.kill(value["pid"], 0)
    except ProcessLookupError:
        return False
    if value.get("process_start") and value.get("boot_id"):
        try:
            observed = Path(f"/proc/{value['pid']}/stat").read_text().rsplit(")", 1)[1].split()
            return (
                observed[0] != "Z"
                and observed[19] == value["process_start"]
                and Path("/proc/sys/kernel/random/boot_id").read_text().strip() == value["boot_id"]
            )
        except FileNotFoundError:
            return False
    return True


def journal(repo: Path) -> Path:
    return repo.resolve() / ".git" / JOURNAL


def restore(repo: Path, *, token: str | None = None) -> dict | None:
    path = journal(repo)
    if not path.exists():
        return None
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise ValueError("Mutation journal storage must not be a symlink")
    value = json.loads(path.read_text())
    if value.get("version") != 1:
        raise ValueError("Unknown evaluator mutation journal")
    if value["token"] != token and alive(value["owner"]):
        raise ValueError("An evaluator still owns this source mutation")
    relative = Path(value["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Mutation journal path must remain inside the candidate")
    file = repo.resolve() / relative
    if file.resolve() != file:
        raise ValueError("Mutation source must not be a symlink")
    original = base64.b64decode(value["original_base64"], validate=True)
    if digest(original) != value["original_sha256"]:
        raise ValueError("Evaluator original-source backup does not match its digest")
    observed = digest(file.read_bytes())
    if observed not in {value["original_sha256"], value["mutant_sha256"]}:
        raise ValueError(
            "Source changed outside the evaluator mutation; preserving journal for review"
        )
    if observed != value["original_sha256"]:
        atomic(file, original, value["original_mode"])
    if digest(file.read_bytes()) != value["original_sha256"]:
        raise ValueError("Evaluator mutation restoration did not preserve original source")
    receipt = {key: value[key] for key in ("path", "original_sha256", "mutant_sha256", "mutation")}
    receipt["restoration"] = (
        "normal-finally" if token == value["token"] else "interrupted-evaluator-recovery"
    )
    atomic(path.with_name("drift-last-mutation-restoration.json"), json.dumps(receipt).encode())
    path.unlink()
    return receipt


@contextmanager
def injected(repo: Path, file: Path, mutated: str, mutation: dict):
    restore(repo)
    root, file = repo.resolve(), file.absolute()
    if file.resolve() != file or not file.is_relative_to(root):
        raise ValueError("Evaluator mutations require a regular candidate source path")
    path = journal(root)
    path.parent.mkdir(exist_ok=True)
    if path.parent.resolve() != path.parent:
        raise ValueError("Evaluator metadata must not be a symlink")
    original = file.read_bytes()
    changed = mutated.encode("utf-8")
    value = {
        "version": 1,
        "token": uuid.uuid4().hex,
        "owner": owner(),
        "path": file.relative_to(root).as_posix(),
        "mutation": mutation,
        "original_base64": base64.b64encode(original).decode("ascii"),
        "original_sha256": digest(original),
        "mutant_sha256": digest(changed),
        "original_mode": stat.S_IMODE(file.stat().st_mode),
    }
    atomic(path, json.dumps(value).encode())
    try:
        atomic(file, changed, value["original_mode"])
        yield value
    finally:
        restore(root, token=value["token"])
