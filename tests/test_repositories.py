"""Repository selection is request-local; MCP and each checkout's state stay independent."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anaxigraph.application import Application
from anaxigraph.interfaces import repositories
from anaxigraph.interfaces.http import create_app


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("ANAXIGRAPH_REPOSITORIES", raising=False)
    root = tmp_path / "repos"
    primary, other = root / "FlowThink", root / "Billing"
    for path, name in ((primary, "primary"), (other, "billing")):
        path.mkdir(parents=True)
        (path / "domain.py").write_text(f"# Comment\ndef {name}():\n    return 1\n\n")
    app = Application(primary, tmp_path / "service-state/flowthink.sqlite")
    options = {"repository_roots": [root], "repository_state": tmp_path / "registered.json"}
    return app, other, options


def client_for(app, **options):
    return TestClient(
        create_app(app, **options), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    )


def connect(client, path):
    response = client.post("/api/repositories", json={"path": str(path)})
    assert response.status_code == 200, response.text
    return response.json()["selected_repository_id"]


def selected(identity):
    return {"X-AnaxiGraph-Repository": identity}


def test_registration_persists_without_scanning_or_creating_a_child_database(
    workspace, monkeypatch
):
    app, other, options = workspace
    monkeypatch.setattr(
        Application, "scan", lambda *args, **kwargs: pytest.fail("Registration scanned source")
    )
    with client_for(app, **options) as client:
        identity = connect(client, other)
        catalog = client.get("/api/repositories").json()
        assert catalog["default_repository_id"] != identity
        entry = next(row for row in catalog["repositories"] if row["id"] == identity)
        assert entry["path"] == str(other)
        assert not (other / ".anaxigraph-v2").exists()
    with client_for(app, **options) as restored:
        assert identity in {
            row["id"] for row in restored.get("/api/repositories").json()["repositories"]
        }
    saved = json.loads(options["repository_state"].read_text())
    assert saved == {"version": 1, "paths": [str(app.root), str(other)]}


def test_selected_http_maps_and_workflow_settings_are_isolated_but_mcp_stays_primary(workspace):
    app, other, options = workspace
    app.scan()
    with client_for(app, **options) as client:
        identity = connect(client, other)
        headers = selected(identity)
        assert client.get("/api/status", headers=headers).json()["snapshot"] is None
        assert client.post("/api/scan", headers=headers).json()["root"] == str(other)
        assert client.get("/api/status").json()["root"] == str(app.root)
        assert client.get("/api/status", headers=headers).json()["root"] == str(other)
        assert client.get("/api/status", headers=headers).json()["source_loc"] == 2
        assert (
            client.get(
                "/api/entity", params={"entity_id": "symbol:domain.py#primary"}, headers=headers
            ).status_code
            == 400
        )
        assert (
            client.post("/api/workflows/config", json={"config": {"workers": 1}}).status_code == 200
        )
        assert (
            client.post(
                "/api/workflows/config", json={"config": {"workers": 2}}, headers=headers
            ).status_code
            == 200
        )
        assert client.get("/api/workflows/config").json()["workers"] == 1
        assert client.get("/api/workflows/config", headers=headers).json()["workers"] == 2
        result = client.post(
            "/mcp",
            headers={**headers, "Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "status", "arguments": {}},
            },
        ).json()["result"]
        assert not result.get("isError", False)
        status = result.get("structuredContent") or json.loads(result["content"][0]["text"])
        assert status["root"] == str(app.root)
    assert app.store.path == options["repository_state"].parent / "service-state/flowthink.sqlite"
    assert (other / ".anaxigraph-v2/knowledge.sqlite").exists()


def test_concurrent_selections_never_change_another_request_or_primary(workspace):
    app, other, options = workspace
    with client_for(app, **options) as client:
        identity = connect(client, other)

        def read(index):
            response = client.get("/api/status", headers=selected(identity) if index % 2 else {})
            assert response.status_code == 200
            return response.json()["root"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            roots = list(pool.map(read, range(20)))
        assert roots == [str(other if n % 2 else app.root) for n in range(20)]
        assert client.get("/api/status").json()["root"] == str(app.root)


@pytest.mark.parametrize(
    "headers,status",
    [
        ({"X-AnaxiGraph-Repository": "unknown"}, 404),
        ({"X-AnaxiGraph-Repository": ""}, 404),
        ([("X-AnaxiGraph-Repository", "a"), ("X-AnaxiGraph-Repository", "b")], 400),
    ],
)
def test_invalid_selection_never_silently_falls_back(workspace, headers, status):
    app, _, options = workspace
    with client_for(app, **options) as client:
        assert client.get("/api/status", headers=headers).status_code == status


def test_guard_precedes_registration_and_lazy_child_initialization(workspace):
    app, other, options = workspace
    with client_for(app, **options) as client:
        assert (
            client.post(
                "/api/repositories",
                headers={"Origin": "https://attacker.example"},
                json={"path": str(other)},
            ).status_code
            == 403
        )
        assert not options["repository_state"].exists()
        identity = connect(client, other)
        assert (
            client.get(
                "/api/status", headers={**selected(identity), "Origin": "https://attacker.example"}
            ).status_code
            == 403
        )
        assert not (other / ".anaxigraph-v2").exists()


def test_outside_relative_and_database_override_paths_are_rejected(workspace, tmp_path):
    app, other, options = workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    with client_for(app, **options) as client:
        for path in (
            "relative/path",
            str(outside),
            str(other / "domain.py"),
            str(other.parent / "missing"),
        ):
            rejected = client.post("/api/repositories", json={"path": path})
            assert rejected.status_code == 400
            assert rejected.json()["detail"]
        assert (
            client.post(
                "/api/repositories", json={"path": str(other), "database": str(app.store.path)}
            ).status_code
            == 422
        )
    assert not options["repository_state"].exists()


def test_symlink_alias_deduplicates_root_but_symlink_state_cannot_share_a_database(
    workspace, tmp_path
):
    app, other, options = workspace
    alias = other.parent / "Alias"
    alias.symlink_to(other, target_is_directory=True)
    with client_for(app, **options) as client:
        identity = connect(client, other)
        assert connect(client, alias) == identity
        (other / ".anaxigraph-v2").symlink_to(app.store.path.parent, target_is_directory=True)
        before = app.store.path.read_bytes()
        assert client.get("/api/status", headers=selected(identity)).status_code == 400
        assert app.store.path.read_bytes() == before


def test_unavailable_registered_root_does_not_replace_primary_or_prevent_service_start(workspace):
    app, other, options = workspace
    with client_for(app, **options) as client:
        identity = connect(client, other)
    (other / "domain.py").unlink()
    other.rmdir()
    with client_for(app, **options) as client:
        assert client.get("/api/status", headers=selected(identity)).status_code == 409
        assert client.get("/api/status").json()["root"] == str(app.root)


def test_developer_assets_come_from_trusted_runtime_and_require_project_identity(workspace):
    app, other, options = workspace
    (other / "pyproject.toml").write_text('[project]\nname="anaxigraph"\n')
    (other / "src/anaxigraph").mkdir(parents=True)
    (other / "src/anaxigraph/__init__.py").write_text("")
    # Executable-looking workspace assets must never be served.
    untrusted = other / "experiments/dashboard_evals/assets"
    untrusted.mkdir(parents=True)
    (untrusted / "evals.js").write_text("window.UNTRUSTED = true")
    trusted = repositories.development_assets(other)
    assert trusted is not None
    with client_for(app, **options) as client:
        identity = connect(client, other)
        base = f"/api/repositories/{identity}/development"
        assert client.get("/api/status", headers=selected(identity)).json()["developer_tools"]
        assert client.get(base + "/evals.js").text == (trusted / "evals.js").read_text()
        assert "UNTRUSTED" not in client.get(base + "/evals.js").text
        assert client.get(base + "/pyproject.toml").status_code == 404
        default = client.get("/api/repositories").json()["default_repository_id"]
        assert client.get(f"/api/repositories/{default}/development/evals.js").status_code == 404


def test_anaxigraph_folder_name_and_distribution_without_source_assets_do_not_enable_tools(
    workspace, monkeypatch
):
    app, other, options = workspace
    (other / "anaxigraph").mkdir()
    assert repositories.development_assets(other) is None
    monkeypatch.setattr(
        repositories, "__file__", str(other / "wheel/anaxigraph/interfaces/repositories.py")
    )
    (other / "pyproject.toml").write_text('[project]\nname="anaxigraph"\n')
    (other / "src/anaxigraph").mkdir(parents=True)
    (other / "src/anaxigraph/__init__.py").write_text("")
    with client_for(app, **options) as client:
        identity = connect(client, other)
        assert not client.get("/api/status", headers=selected(identity)).json()["developer_tools"]


@pytest.mark.parametrize("metadata", ['project = "unexpected"\n', "project = []\n", "[project\n"])
def test_unrecognized_project_metadata_does_not_break_status(workspace, metadata):
    app, other, options = workspace
    (other / "pyproject.toml").write_text(metadata)
    with client_for(app, **options) as client:
        identity = connect(client, other)
        response = client.get("/api/status", headers=selected(identity))
        assert response.status_code == 200
        assert response.json()["developer_tools"] is False


def test_async_scan_status_and_result_stay_with_selected_repository(workspace):
    app, other, options = workspace
    with client_for(app, **options) as client:
        headers = selected(connect(client, other))
        assert client.post("/api/scan/start", headers=headers).status_code == 202
        deadline = time.monotonic() + 5
        result = client.get("/api/scan/status", headers=headers).json()
        while result["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
            result = client.get("/api/scan/status", headers=headers).json()
        assert result["status"] == "completed", result
        assert result["result"]["root"] == str(other)
        assert client.get("/api/scan/status").json()["status"] == "idle"
        assert client.get("/api/status").json()["snapshot"] is None


def test_failed_registry_write_keeps_previous_connections_without_temporary_files(
    workspace, monkeypatch
):
    app, other, options = workspace
    extra = other.parent / "Another"
    extra.mkdir()
    with client_for(app, **options) as client:
        identity = connect(client, other)
        saved = options["repository_state"].read_bytes()

        def refused(self, target):
            raise OSError("Registry is read-only")

        monkeypatch.setattr(Path, "replace", refused)
        response = client.post("/api/repositories", json={"path": str(extra)})
        assert response.status_code == 400
        assert "read-only" in response.json()["detail"]
        assert options["repository_state"].read_bytes() == saved
        rows = client.get("/api/repositories").json()["repositories"]
        assert {row["id"] for row in rows} == {identity, client.app.state.repositories.default}
        assert not list(options["repository_state"].parent.glob("tmp*"))
