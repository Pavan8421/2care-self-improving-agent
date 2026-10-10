"""FastAPI entrypoint: serves chat API and static tiny web UI."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent.graph import SessionManager
from app.api.chat import router as chat_router
from app.clinic.store import default_store
from app.config import BACKEND_ROOT, get_settings

FRONTEND_DIR = BACKEND_ROOT.parent / "frontend"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Clinic Scheduling Agent", version="0.1.0")
    app.state.sessions = SessionManager(store=default_store)
    app.state.settings = settings

    app.include_router(chat_router)

    @app.get("/api/health")
    def health() -> dict:
        return {
            "ok": True,
            "key_present": settings.has_openai_api_key(),
            "agent_model": settings.agent_model,
        }

    if FRONTEND_DIR.is_dir():
        # Explicit index so / always hits the chat UI before StaticFiles catch-all.
        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "index.html")

        app.mount(
            "/src",
            StaticFiles(directory=FRONTEND_DIR / "src"),
            name="frontend-src",
        )
        app.mount(
            "/assets",
            StaticFiles(directory=FRONTEND_DIR),
            name="frontend-assets",
        )

    return app


app = create_app()


def main() -> None:
    """`python -m app.main` — start uvicorn with settings host/port."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
