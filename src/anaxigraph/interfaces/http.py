"""HTTP and packaged dashboard access to the shared application."""

from __future__ import annotations

import base64
import binascii
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field

from anaxigraph.interfaces.mcp import create_mcp

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _origin(value: str) -> str:
    url = urlsplit(value)
    invalid = (
        url.scheme not in {"http", "https"},
        not url.hostname,
        url.username is not None,
        url.password is not None,
        url.path not in {"", "/"},
        bool(url.query),
        bool(url.fragment),
        any(c.isspace() for c in value),
    )
    if any(invalid):
        raise ValueError("HTTP origins must be exact http(s) URLs without credentials or paths")
    port = url.port
    host = f"[{url.hostname}]" if ":" in str(url.hostname) else url.hostname
    suffix = f":{port}" if port not in (None, {"http": 80, "https": 443}[url.scheme]) else ""
    return f"{url.scheme}://{host}{suffix}"


def _source_status(request: Request, origins: set[str]) -> int:
    host = request.headers.get("host", "")
    if (
        len(request.headers.getlist("host")) != 1
        or "/" in host
        or len(request.headers.getlist("origin")) > 1
    ):
        return 421
    try:
        target = _origin(f"{request.scope['scheme']}://{host}")
        if urlsplit(target).hostname not in _LOOPBACK and target not in origins:
            return 421
        origin = request.headers.get("origin")
        if origin and _origin(origin) not in origins | {target}:
            return 403
    except ValueError:
        return 403
    return 403 if not origin and request.headers.get("sec-fetch-site") == "cross-site" else 0


def _authorized(request: Request, token: str) -> bool:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "basic":
        try:
            user, separator, value = base64.b64decode(value, validate=True).decode().partition(":")
        except (ValueError, UnicodeError, binascii.Error):
            return False
        if user != "anaxigraph" or not separator:
            return False
    elif scheme.lower() != "bearer":
        return False
    return secrets.compare_digest(value.encode(), token.encode())


class _HTTPGuard:
    def __init__(self, app, origins: set[str], token: str | None):
        self.app, self.origins, self.token = app, origins, token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        status = _source_status(request, self.origins)
        if (
            not status
            and self.token is None
            and (request.client is None or request.client.host not in _LOOPBACK)
        ):
            status = 403
        if not status and self.token is not None and not _authorized(request, self.token):
            return await JSONResponse(
                {"detail": "Authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="AnaxiGraph"'},
            )(scope, receive, send)
        if status:
            return await JSONResponse(
                {"detail": "Untrusted HTTP host or origin"}, status_code=status
            )(scope, receive, send)
        return await self.app(scope, receive, send)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskInput(Input):
    task: str = Field(min_length=1, max_length=8000)
    limit: int = Field(default=12, ge=1, le=50)
    baseline_id: str | None = Field(default=None, max_length=200)
    continue_task: bool = False


class PlanInput(Input):
    task: str = Field(min_length=1, max_length=8000)
    files: list[str] = Field(max_length=100)
    decisions: list[dict[str, Any]] | None = Field(default=None, max_length=30)


class ValidationInput(Input):
    receipts: list[dict[str, Any]] | None = Field(default=None, max_length=100)
    baseline_id: str | None = Field(default=None, max_length=200)


class CIPlanInput(Input):
    base_ref: str | None = Field(default=None, max_length=200)
    base_snapshot_id: str | None = Field(default=None, max_length=200)
    changed_paths: list[str] | None = Field(default=None, max_length=10000)
    cadence: str = Field(default="change", pattern="^(change|release|scheduled)$")
    runtime_context: dict[str, Any] | None = None


class AnalysisInput(Input):
    kind: str = Field(
        pattern="^(annotate|pattern-screen|patterns|architecture|delivery|fresh-eyes)$"
    )
    scope: list[str] | None = Field(default=None, max_length=100)
    pattern_ids: list[str] | None = Field(default=None, max_length=133)
    lens: str | None = Field(default=None, pattern="^(boundaries|packages|verification)$")


class SubmissionInput(Input):
    request_id: str = Field(min_length=1, max_length=200)
    result: dict[str, Any]


class WorkflowInput(Input):
    kind: str = Field(pattern="^(mapping|annotate|patterns|architecture|ci|delivery|fresh-eyes)$")
    config: dict[str, Any] | None = None
    scope: list[str] | None = None


class ConfigurationInput(Input):
    config: dict[str, Any]


def create_app(
    application: Any, *, allowed_origins: list[str] | None = None, auth_token: str | None = None
) -> FastAPI:
    origins = {_origin(value) for value in allowed_origins or []}
    if auth_token is not None and len(auth_token) < 32:
        raise ValueError("HTTP auth token must contain at least 32 characters")
    if any(urlsplit(value).hostname not in _LOOPBACK for value in origins) and auth_token is None:
        raise ValueError("Non-loopback HTTP origins require an auth token")
    mcp = create_mcp(application)
    local_hosts = [
        value for host in ("127.0.0.1", "localhost", "[::1]") for value in (host, host + ":*")
    ]
    hosts = list(local_hosts)
    sdk_origins = set(origins)
    for origin in origins:
        url = urlsplit(origin)
        hosts.append(url.netloc)
        if url.port is None:
            authority = f"{url.netloc}:{443 if url.scheme == 'https' else 80}"
            hosts.append(authority)
            sdk_origins.add(f"{url.scheme}://{authority}")
    mcp.settings.transport_security = TransportSecuritySettings(
        allowed_hosts=hosts,
        allowed_origins=[
            scheme + "://" + host for scheme in ("http", "https") for host in local_hosts
        ]
        + sorted(sdk_origins),
    )
    transport = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_server: FastAPI):
        async with mcp.session_manager.run():
            yield

    server = FastAPI(
        title="AnaxiGraph V2",
        description="One shared map for humans and coding agents",
        lifespan=lifespan,
    )
    server.add_middleware(_HTTPGuard, origins=origins, token=auth_token)
    assets = Path(__file__).parent / "assets"
    server.mount("/assets", StaticFiles(directory=assets), name="assets")

    @server.exception_handler(ValueError)
    async def invalid_request(_request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @server.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(assets / "index.html")

    @server.get("/api/status")
    def status() -> dict:
        return application.status()

    @server.get("/api/modules")
    def modules(
        sort: str = "path",
        descending: bool = False,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        return application.modules(sort, descending, limit, offset)

    @server.get("/api/entity")
    def entity(entity_id: str, limit: int = Query(default=30, ge=1, le=100)) -> dict:
        return application.inspect(entity_id, limit)

    @server.get("/api/reviews")
    def reviews(
        kind: str | None = None,
        limit: int = Query(default=12, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        if limit != 12 or offset:
            return application.reviews(kind, limit, offset)
        return application.reviews(kind)

    @server.post("/api/scan")
    def scan() -> dict:
        return application.scan()

    @server.post("/api/prepare")
    def prepare(body: TaskInput) -> dict:
        if body.continue_task:
            return application.prepare_change(body.task, body.limit, body.baseline_id, True)
        if body.baseline_id:
            return application.prepare_change(body.task, body.limit, body.baseline_id)
        return application.prepare_change(body.task, body.limit)

    @server.post("/api/assess")
    def assess(body: PlanInput) -> dict:
        if body.decisions is not None:
            return application.assess_plan(body.task, body.files, body.decisions)
        return application.assess_plan(body.task, body.files)

    @server.post("/api/validate")
    def validate(body: ValidationInput) -> dict:
        if body.baseline_id:
            return application.validate_change(body.receipts, body.baseline_id)
        return application.validate_change(body.receipts)

    @server.post("/api/ci/plan")
    def ci_plan(body: CIPlanInput) -> dict:
        return application.ci_plan(**body.model_dump())

    @server.post("/api/analysis/request")
    def analysis_request(body: AnalysisInput) -> dict:
        options: dict[str, Any] = {}
        if body.pattern_ids is not None:
            options["pattern_ids"] = body.pattern_ids
        if body.lens is not None:
            options["lens"] = body.lens
        return application.analysis_request(body.kind, body.scope, **options)

    @server.post("/api/analysis/submit")
    def submit(body: SubmissionInput) -> dict:
        return application.submit_analysis(body.request_id, body.result)

    @server.get("/api/workflows")
    def workflows(
        run_id: str | None = None,
        batch_offset: int = Query(default=0, ge=0, le=100_000),
        batch_limit: int = Query(default=10, ge=1, le=50),
    ) -> dict:
        return application.workflow_status(run_id, batch_offset, batch_limit)

    @server.get("/api/workflows/config")
    def workflow_config() -> dict:
        return application.workflow_configure()

    @server.post("/api/workflows/config")
    def save_workflow_config(body: ConfigurationInput) -> dict:
        return application.workflow_configure(body.config)

    @server.post("/api/workflows/start")
    def workflow_start(body: WorkflowInput) -> dict:
        return application.workflow_start(body.kind, body.config, body.scope)

    @server.post("/api/workflows/{run_id}/resume")
    def workflow_resume(run_id: str) -> dict:
        return application.workflow_resume(run_id)

    @server.get("/api/record")
    def record_status() -> dict:
        return application.portable_status()

    @server.post("/api/record/export")
    def export_record() -> dict:
        return application.export_record()

    @server.post("/api/record/import")
    def import_record() -> dict:
        return application.import_record()

    @server.get("/api/evidence")
    def trial_evidence(
        limit: int = Query(default=50, ge=1, le=1000), offset: int = Query(default=0, ge=0)
    ) -> dict:
        return application.trial_evidence(limit, offset)

    @server.post("/api/evidence/export")
    def export_evidence() -> dict:
        return application.export_evidence()

    # Existing REST/assets routes win; the transport handles /mcp unchanged.
    server.mount("/", transport)
    return server
