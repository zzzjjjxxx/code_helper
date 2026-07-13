from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from assistant_shared.models import ArtifactRecord, RollbackResponse, TaskCreateRequest, TaskDetail, TaskRunResponse, TaskSummary

from apps.api.core.deps import get_container
from apps.api.core.container import AppContainer

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskDetail, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreateRequest, container: AppContainer = Depends(get_container)) -> TaskDetail:
    return container.task_service.create_task(payload)


@router.get("", response_model=list[TaskSummary])
def list_tasks(container: AppContainer = Depends(get_container)) -> list[TaskSummary]:
    return container.task_service.list_tasks()


@router.get("/{task_id}", response_model=TaskDetail)
def get_task(task_id: str, container: AppContainer = Depends(get_container)) -> TaskDetail:
    try:
        return container.task_service.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/run", response_model=TaskRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_task(task_id: str, container: AppContainer = Depends(get_container)) -> TaskRunResponse:
    try:
        return await container.task_service.run_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/rollback", response_model=RollbackResponse)
async def rollback_task(task_id: str, container: AppContainer = Depends(get_container)) -> RollbackResponse:
    try:
        return await container.task_service.rollback(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{task_id}/artifacts", response_model=list[ArtifactRecord])
def list_task_artifacts(task_id: str, container: AppContainer = Depends(get_container)) -> list[ArtifactRecord]:
    try:
        container.task_service.get_task(task_id)
        return container.task_service.list_artifacts(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
