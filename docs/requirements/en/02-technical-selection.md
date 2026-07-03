# Technical Selection

## 1. Architecture

```text
Client Layer
  Web workspace
      |
API Layer
  Auth / rate limit / logs / routing
      |
Core Service Layer
  Chat service / context service / code modification service / task service
      |
Infrastructure Layer
  SQLite or PostgreSQL / Redis optional / local file index / vector index optional
      |
Model Layer
  Local model / third-party model API / pluggable model adapters
```

## 2. Core Modules

| Module | Responsibility | MVP Recommendation |
| --- | --- | --- |
| Chat service | Manage sessions, call models, stream responses | FastAPI + SSE |
| Context service | Read files, retrieve snippets, trim context | File index + simple retrieval |
| Code modification service | Generate change suggestions and previews | Diff generation and validation |
| Task service | Track task status, latency, errors | SQLite first, PostgreSQL later |
| Safety service | Rate limit, sensitive data detection, log masking | Rules and middleware |

## 3. Backend Framework Comparison

MVP backend needs streaming responses, async model calls, clean API structure, local deployment, and future plugin extensibility.

| Option | Strengths | Weaknesses | Fit |
| --- | --- | --- | --- |
| FastAPI | Native async, type-friendly, automatic OpenAPI, direct SSE/WebSocket support, strong fit for AI APIs | Admin/back-office features weaker than Django; project structure must be defined by us | High |
| Django + Ninja | Mature ecosystem, ORM, admin, strong for complex business systems | Heavier; async and streaming are less direct than FastAPI | Medium |
| Flask | Lightweight, simple, mature ecosystem | Type safety, async, OpenAPI, and structure require extra work | Medium-low |
| Node.js + NestJS | Strong modularity, good WebSocket ecosystem, same language as frontend | More friction with Python AI, parsing, and local model tooling | Medium |
| Go + Gin | Strong performance, simple deployment, good concurrency | AI SDKs, code analysis, and iteration speed are less convenient than Python | Medium |

## 4. Recommendation

Use FastAPI for the MVP backend skeleton.

Reasons:

1. Lowest integration cost with Python AI tooling, code parsing, vector retrieval, and local model workflows.
2. Native async support fits long-running third-party model API calls and streaming responses.
3. Automatic OpenAPI docs make Web, CLI, and future plugins easier to integrate.
4. Lightweight enough for open-source local deployment and user customization.

## 5. FastAPI Skeleton Scope

If FastAPI is confirmed, the MVP skeleton should contain:

| Module | Content |
| --- | --- |
| app/api | Chat, project, model config, task status HTTP APIs |
| app/core | Config, logging, error handling, dependency wiring |
| app/models | Session, Message, Task, Feedback data structures |
| app/services | Chat orchestration, context retrieval, model adapters, code modification |
| app/adapters | Third-party model APIs, local models, filesystem adapters |
| app/storage | SQLite/PostgreSQL storage, project index, cache |
| tests | API, service layer, model adapter tests |

First skeleton should include:

1. FastAPI app entry and health check.
2. Model configuration API for local models and third-party API keys.
3. Chat API with streaming placeholder.
4. Project file read API limited to the configured local workspace path.
5. Basic task status API.

## 6. Data Model

```python
class Session:
    id: str
    user_id: str
    project_id: str
    created_at: str
    updated_at: str


class Message:
    id: str
    session_id: str
    role: str
    content: str
    context_files: list[str]
    created_at: str
    tokens_used: int


class Task:
    id: str
    user_id: str
    session_id: str
    type: str
    status: str
    error: str | None
    started_at: str
    completed_at: str | None


class Feedback:
    id: str
    message_id: str
    rating: int
    accepted: bool
    comment: str | None
```

## 7. Context Strategy

MVP does not implement complex long-term memory. It uses three context layers:

| Layer | Content | Lifetime |
| --- | --- | --- |
| Current context | Current file, selected code, user input | Single request |
| Session context | Latest 10 turns and key summary | Current session |
| Project context | File tree, related snippets, index summary | Project lifecycle |

Context priority:

1. User input.
2. Current file or selected code.
3. Recent conversation.
4. Retrieved related snippets.
5. Project structure summary.

## 8. Initial Technology Choices

| Area | MVP Choice | Notes |
| --- | --- | --- |
| Backend | FastAPI | Recommended |
| API streaming | SSE first | WebSocket can be added later |
| Storage | SQLite first | PostgreSQL can be added for team/remote mode |
| Cache | In-memory first | Redis optional later |
| Model adapters | OpenAI-compatible API + local model adapter | Provider-specific adapters later |
| File index | Local filesystem index | Vector index optional |
| Tests | pytest | API and service tests first |
