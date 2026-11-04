"""Explicit local dashboard workspaces; MCP keeps its configured Application."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import tomllib
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


def development_assets(root: Path) -> Path | None:
    trusted = Path(__file__).resolve().parents[3]
    if trusted / "src/anaxigraph/interfaces/repositories.py" != Path(__file__).resolve():
        return None
    assets = trusted / "experiments/dashboard_evals/assets"
    try:
        project = root / "pyproject.toml"
        own = (
            project.stat().st_size < 65_536
            and tomllib.loads(project.read_text())["project"]["name"] == "anaxigraph"
        )
        if (
            own
            and (root / "src/anaxigraph/__init__.py").is_file()
            and assets.resolve().is_relative_to(trusted)
        ):
            if all(
                (assets / name).is_file() and not (assets / name).is_symlink()
                for name in ("evals.html", "evals.js", "evals.css")
            ):
                return assets
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


class Repositories:
    def __init__(self, application, *, roots=None, paths=None, state=None):
        self.lock = threading.RLock()
        root = application.root.resolve()
        self.default = self.identity(root)
        database = getattr(
            getattr(application, "store", None), "path", root / ".anaxigraph-v2/knowledge.sqlite"
        )
        self.state = Path(state or database.with_suffix(".repositories.json")).absolute()
        configured = (
            roots
            if roots is not None
            else os.environ.get("ANAXIGRAPH_REPOSITORY_ROOTS", "").split(os.pathsep)
        )
        default = (
            root.parent if root.parent.name in {"repos", "repositories", "worktrees"} else root
        )
        self.roots = [Path(path).resolve() for path in configured if path] or [default]
        self.paths = {self.default: root}
        self.apis: dict = {}
        self._load()
        configured = (
            paths
            if paths is not None
            else os.environ.get("ANAXIGRAPH_REPOSITORIES", "").split(os.pathsep)
        )
        for path in configured:
            if path:
                self.add(path, persist=False)

    def _load(self):
        if self.state.exists():
            if self.state.is_symlink() or self.state.stat().st_size > 131_072:
                raise ValueError("Repository registry must be a bounded regular local file")
            saved = json.loads(self.state.read_text())
            if saved.get("version") != 1 or not isinstance(saved.get("paths"), list):
                raise ValueError("Unsupported repository registry")
            for path in saved["paths"]:
                self.add(path, persist=False, existing=False)

    @staticmethod
    def identity(path: Path) -> str:
        return hashlib.sha256(str(path).encode()).hexdigest()[:20]

    def add(self, path, *, persist=True, existing=True):
        value = Path(path)
        if not value.is_absolute() or len(str(value)) > 4096:
            raise ValueError("Repository path must be an absolute local directory")
        value = value.resolve(strict=existing)
        if value == self.paths[self.default]:
            return self.default
        if (existing and not value.is_dir()) or not any(
            value.is_relative_to(root) for root in self.roots
        ):
            raise ValueError("Repository must be inside the configured local repository roots")
        identity = self.identity(value)
        with self.lock:
            paths = self.paths | {identity: value}
            if len(paths) > 32:
                raise ValueError("At most 32 repositories can be connected")
            if persist:
                self._save(paths)
            self.paths = paths
        return identity

    def _save(self, paths):
        body = json.dumps(
            {"version": 1, "paths": list(map(str, paths.values()))}, ensure_ascii=False
        )
        if len(body.encode()) > 131_072:
            raise ValueError("Repository registry exceeds its 128 KiB budget")
        if self.state.is_symlink() or self.state.parent.resolve() != self.state.parent:
            raise ValueError("Repository registry storage cannot be a symlink")
        self.state.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=self.state.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.state)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def root(self, identity):
        if identity not in self.paths:
            raise HTTPException(404, "Unknown repository")
        path = self.paths[identity]
        if not path.is_dir() or path.resolve() != path:
            raise HTTPException(409, "Connected repository is unavailable or moved")
        return path

    def catalog(self):
        return {
            "default_repository_id": self.default,
            "repositories": [
                {
                    "id": identity,
                    "name": path.name,
                    "path": str(path),
                }
                for identity, path in self.paths.items()
            ],
        }

    def api(self, identity, factory):
        from anaxigraph.application import Application

        with self.lock:
            path = self.root(identity)
            if identity not in self.apis:
                database = path / ".anaxigraph-v2/knowledge.sqlite"
                if database.resolve() != database:
                    raise ValueError("Each repository needs its own local state directory")
                self.apis[identity] = factory(Application(path))
            return self.apis[identity]


class RepositorySelection:
    def __init__(self, app, registry, factory):
        self.app, self.registry, self.factory = app, registry, factory

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "").removeprefix(scope.get("root_path", ""))
        if (
            scope["type"] == "http"
            and path.startswith("/api/")
            and not path.startswith("/api/repositories")
        ):
            values = Request(scope).headers.getlist("x-anaxigraph-repository")
            try:
                if len(values) > 1:
                    raise HTTPException(400, "Supply one repository identity")
                if values and values[0] != self.registry.default:
                    selected = self.registry.api(values[0], self.factory)
                    return await selected(scope, receive, send)
            except HTTPException as error:
                return await JSONResponse({"detail": error.detail}, status_code=error.status_code)(
                    scope, receive, send
                )
            except (ValueError, OSError) as error:
                return await JSONResponse({"detail": str(error)}, status_code=400)(
                    scope, receive, send
                )
        return await self.app(scope, receive, send)
