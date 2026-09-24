# API

## Conventions

<!-- TODO: Base URL, versioning, auth scheme, content type. -->

## Authentication

<!-- TODO: How a client authenticates. Token lifetime, refresh. -->

## Errors

<!-- TODO: Error envelope shape, status code usage, error codes. -->

### Input validation (AP34)

Request bodies are bounded by Pydantic `Field`/`Literal` constraints declared on the models in
`backend/routes.py`. A violation returns FastAPI's standard `422` shape —
`{"detail": [{"loc": [...], "msg": "...", ...}]}` — before the handler runs (so, for
`POST /api/generate`, before any model-generation call is made):

| Field | Constraint |
|---|---|
| `LearningPathCreate.goal` | 3–2000 characters |
| `LearningPathCreate.experience_level` | one of `beginner`, `intermediate`, `advanced` |
| `LearningPathCreate.time_commitment` | one of `1-5 hours/week`, `5-10 hours/week`, `10-20 hours/week`, `20+ hours/week` |
| `DifficultyFeedback.feedback` | one of `too_easy`, `just_right`, `too_hard` |
| `MilestoneTaskCreate.title` / `MilestoneTaskUpdate.title` | 1–200 / ≤200 characters |
| `NoteUpsert.content` | ≤10,000 characters (empty/whitespace is a valid submission — it deletes the note) |

The `goal`/`experience_level`/`time_commitment` values that reach the model prompt are wrapped in
a `<<<LEARNER_INPUT_START>>> … <<<LEARNER_INPUT_END>>>` block with a system-message line stating
that block is learner data, never an instruction — mitigation against prompt injection, not a
guarantee.

## Pagination, filtering, sorting

<!-- TODO: Standard query params and response shape. -->

## Endpoints

<!-- TODO: Link to the OpenAPI/Swagger spec once it exists. Until then, list the key endpoints here. -->

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| <!-- TODO --> | <!-- TODO --> | <!-- TODO --> | <!-- TODO --> |
