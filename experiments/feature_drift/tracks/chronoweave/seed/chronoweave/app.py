from fastapi import FastAPI

from chronoweave import errors
from chronoweave.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="ChronoWeave")
    app.state.settings = settings
    errors.install(app)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
