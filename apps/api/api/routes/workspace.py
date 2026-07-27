from __future__ import annotations

from fastapi import APIRouter, HTTPException

from apps.api.services.workspace_picker import pick_workspace

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.post("/pick")
def pick_workspace_folder() -> dict[str, str | None]:
    try:
        return {"path": pick_workspace()}
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Native workspace picker failed.") from exc
