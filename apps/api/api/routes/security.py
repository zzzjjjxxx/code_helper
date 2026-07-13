from __future__ import annotations

from fastapi import APIRouter, Depends

from assistant_shared.models import SecurityPolicy

from apps.api.core.container import AppContainer
from apps.api.core.deps import get_container

router = APIRouter(prefix="/security", tags=["security"])


@router.get("/policy", response_model=SecurityPolicy)
def get_security_policy(container: AppContainer = Depends(get_container)) -> SecurityPolicy:
    return container.settings.security_policy()
