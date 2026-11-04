from fastapi import FastAPI

from aetherloom import errors
from aetherloom.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="AetherLoom")
    app.state.settings = settings
    errors.install(app)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
