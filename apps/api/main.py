from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.api.routes.events import router as events_router
from apps.api.api.routes.security import router as security_router
from apps.api.api.routes.tasks import router as tasks_router
from apps.api.api.routes.workspace import router as workspace_router
from apps.api.core.config import get_settings
from apps.api.core.container import AppContainer
from apps.api.services.event_service import EventService
from apps.api.services.rollback_service import RollbackService
from apps.api.services.task_service import TaskService
from apps.api.storage.sqlite import SQLiteStore


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def _startup() -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.snapshot_root.mkdir(parents=True, exist_ok=True)
        settings.workspace_root.mkdir(parents=True, exist_ok=True)
        store = SQLiteStore(settings.database_path)
        store.initialize()
        events = EventService(store)
        rollback_service = RollbackService(store, events)
        task_service = TaskService(
            settings=settings,
            store=store,
            events=events,
            rollback_service=rollback_service,
        )
        app.state.container = AppContainer(
            settings=settings,
            store=store,
            events=events,
            rollback_service=rollback_service,
            task_service=task_service,
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(tasks_router)
    app.include_router(workspace_router)
    app.include_router(events_router)
    app.include_router(security_router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("apps.api.main:app", host="127.0.0.1", port=8000, reload=False)
