# Architecture

The MVP now uses a bounded collaborative workflow:

1. planner agent inspects the task and chooses a branch
2. executor agent reads code, captures a snapshot, applies a patch, and runs tests
3. reviewer agent checks the branch and either approves it or requests a replan
4. the workflow can roll back a rejected branch and try another one
5. events, artifacts, and memory are persisted for replay and inspection

The backend keeps task state in SQLite and publishes task events over SSE. The frontend consumes REST for task state and SSE for live updates, including the planner/executor/reviewer collaboration trace.
