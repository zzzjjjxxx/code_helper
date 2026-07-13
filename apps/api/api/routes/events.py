from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from apps.api.core.container import AppContainer
from apps.api.core.deps import get_container

router = APIRouter(tags=["events"])


@router.get("/tasks/{task_id}/events")
async def stream_task_events(task_id: str, request: Request, after: int = 0, container: AppContainer = Depends(get_container)):
    try:
        container.task_service.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def generator():
        async for chunk in container.events.stream(task_id, after_sequence=after):
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
