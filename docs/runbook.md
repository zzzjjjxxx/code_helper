# Runbook

## Backend

```bash
python -m uvicorn apps.api.main:app --reload
```

The backend serves:
- `GET /health`
- `POST /tasks`
- `GET /tasks`
- `GET /tasks/{task_id}`
- `POST /tasks/{task_id}/run`
- `POST /tasks/{task_id}/rollback`
- `GET /tasks/{task_id}/events`

## Frontend

```bash
cd apps/web
npm install
npm run dev
```

The console expects the API at `http://127.0.0.1:8000` by default.

## Demo flow

1. Create the seeded demo task.
2. Click **Run task**.
3. Watch the event timeline and diff populate.
4. Inspect the test logs.
5. Click **Roll back** to restore the snapshot.
