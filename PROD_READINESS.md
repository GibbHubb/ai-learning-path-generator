# ai-learning-path-generator — Prod Readiness

Stack: Python · FastAPI · React · SQLite · OpenAI

## Tasks (ordered — do 1/day)

### 1. Async FastAPI + streaming LLM responses
- Convert blocking OpenAI calls to async (`httpx` / official async client)
- SSE endpoint streaming path generation token-by-token to React
- `asyncio.TaskGroup` for parallel sub-path generation, cancellation on client disconnect
- Benchmark p95 time-to-first-token, before/after in README

### 2. SQLite → Postgres + repository pattern
- Data access in `repositories/` with Protocols (not ABCs) — duck-typed, testable
- Alembic migrations, versioned from scratch
- `docker-compose.yml` for local dev (Postgres + pgAdmin)
- DI via FastAPI `Depends`, no global session
- Integration tests via `testcontainers-python`

### 3. Prompt engineering + evals pipeline
- Structured outputs via `response_format={"type":"json_schema",...}` with Pydantic
- Prompt versioning in `prompts/` with git-tracked changelog
- Eval harness: pytest dataset of `(goal, expected_skills)` — embedding cosine + LLM-as-judge
- Cost + latency per request, OpenTelemetry traces exported
