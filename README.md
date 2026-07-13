# R&D Assistant MVP

A resume-demo monorepo for a Python + React R&D assistant.

## What it does
- reads a workspace
- coordinates planner / executor / reviewer agents
- identifies a bug
- applies a patch
- runs tests
- streams events and logs
- supports rollback to a saved snapshot

## Layout
- `apps/api` — FastAPI backend
- `apps/web` — React console
- `packages/*` — shared Python helpers for workflow, telemetry, tools, memory, and shared models
- `data/demo_workspace` — seeded buggy workspace for the demo loop

## Run locally
Backend:

```bash
python -m pip install fastapi uvicorn pydantic pytest httpx
python -m uvicorn apps.api.main:app --reload
```

Frontend:

```bash
cd apps/web
npm install
npm run dev
```

The frontend expects the API at `http://127.0.0.1:8000` by default.
