import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.models.mock import MockModelAdapter
from app.main import create_app
from app.schemas.chat import ChatCompletionRequest, ChatMessage
from app.services.chat import ChatService
from app.services.context_budget import ContextBudgetPlanner
from app.services.persistent_memory import MemoryStoreError, SQLiteSessionMemoryStore
from app.services.session_memory import InMemorySessionMemoryStore
from app.services.token_counter import EstimatedTokenCounter, build_token_counter


def test_token_counter_uses_exact_known_model_and_marks_unknown_model_estimated() -> None:
    exact = build_token_counter("openai_compatible", "gpt-4o-mini")
    fallback = build_token_counter("openai_compatible", "not-a-real-model")

    assert exact.estimated is False
    assert exact.counter_id.startswith("tiktoken:gpt-4o-mini:")
    assert fallback.estimated is True
    assert fallback.counter_id.startswith("estimated:")


async def test_context_budget_rejects_required_context_overflow() -> None:
    planner = ContextBudgetPlanner(
        token_counter=EstimatedTokenCounter(),
        context_window=30,
        default_output_tokens=10,
        protocol_margin_ratio=0,
    )
    service = ChatService(
        model_adapter=MockModelAdapter(),
        default_model="mock-model",
        context_budget_planner=planner,
    )

    events = [
        event
        async for event in service.stream_completion(
            ChatCompletionRequest(message="必须保留的当前请求" * 20)
        )
    ]

    assert "event: error" in events[0]
    assert "CONTEXT_BUDGET_EXCEEDED" in events[0]


async def test_in_memory_budget_keeps_complete_recent_turns() -> None:
    store = InMemorySessionMemoryStore(max_turns=3)
    counter = EstimatedTokenCounter()
    await store.append_turn("session", "old user", "old assistant")
    await store.append_turn("session", "new user", "new assistant")

    newest_pair = [
        ChatMessage(role="user", content="new user"),
        ChatMessage(role="assistant", content="new assistant"),
    ]
    messages = await store.get_messages(
        "session",
        token_counter=counter,
        token_budget=counter.count_messages(newest_pair),
    )

    assert [message.content for message in messages] == ["new user", "new assistant"]


async def test_memory_does_not_replace_oversized_newest_turn_with_stale_turn() -> None:
    store = InMemorySessionMemoryStore(max_turns=3)
    counter = EstimatedTokenCounter()
    await store.append_turn("session", "old", "small")
    await store.append_turn("session", "new" * 100, "large" * 100)

    old_pair = [
        ChatMessage(role="user", content="old"),
        ChatMessage(role="assistant", content="small"),
    ]
    messages = await store.get_messages(
        "session",
        token_counter=counter,
        token_budget=counter.count_messages(old_pair),
    )

    assert messages == []


async def test_irrelevant_global_general_preference_is_not_injected(tmp_path: Path) -> None:
    store = SQLiteSessionMemoryStore(tmp_path / "memory.sqlite3", tmp_path / "project")
    await store.append_turn("old", "以后请始终使用 frobnicator 工具。", "收到")

    messages = await store.get_messages("new", current_user_message="解释这个函数")

    assert all("frobnicator" not in message.content for message in messages)


async def test_current_request_overrides_global_preference(tmp_path: Path) -> None:
    store = SQLiteSessionMemoryStore(tmp_path / "memory.sqlite3", tmp_path / "project")
    await store.append_turn("old", "以后请始终使用中文回答。", "收到")

    messages = await store.get_messages("new", current_user_message="请用英文回答")

    assert all("以后请始终使用中文回答" not in message.content for message in messages)


async def test_project_preference_overrides_global_preference(tmp_path: Path) -> None:
    store = SQLiteSessionMemoryStore(tmp_path / "memory.sqlite3", tmp_path / "project")
    await store.append_turn("global", "以后请始终使用英文回答。", "收到")
    await store.append_turn("project", "本项目请始终使用中文回答。", "收到")

    messages = await store.get_messages("new")
    preferences = await store.list_preferences()

    assert any("本项目请始终使用中文回答" in message.content for message in messages)
    assert all("以后请始终使用英文回答" not in message.content for message in messages)
    assert len([item for item in preferences if item.active]) == 2


async def test_new_l1_directive_filters_conflicting_l2_line(tmp_path: Path) -> None:
    store = SQLiteSessionMemoryStore(
        tmp_path / "memory.sqlite3",
        tmp_path / "project",
        max_turns=1,
    )
    await store.append_turn("session", "以后请始终使用中文回答。", "收到")
    await store.append_turn("session", "以后请始终使用英文回答。", "已切换")

    messages = await store.get_messages("session")

    assert all("以后请始终使用中文回答" not in message.content for message in messages)
    assert any("以后请始终使用英文回答" in message.content for message in messages)


async def test_persistent_turn_redacts_sensitive_content(tmp_path: Path) -> None:
    store = SQLiteSessionMemoryStore(tmp_path / "memory.sqlite3", tmp_path / "project")
    await store.append_turn(
        "session",
        "以后默认 password=super-secret-value",
        "Authorization: Bearer assistant-secret",
    )

    messages = await store.get_messages("session")
    persisted = "\n".join(message.content for message in messages)

    assert "super-secret-value" not in persisted
    assert "assistant-secret" not in persisted
    assert "[REDACTED]" in persisted
    assert await store.list_preferences() == []


async def test_request_id_makes_turn_write_idempotent(tmp_path: Path) -> None:
    store = SQLiteSessionMemoryStore(tmp_path / "memory.sqlite3", tmp_path / "project")
    await store.append_turn("session", "user", "assistant", request_id="request-1")
    await store.append_turn("session", "user", "assistant", request_id="request-1")

    messages = await store.get_messages("session")

    assert [message.content for message in messages] == ["user", "assistant"]


async def test_conflicting_preferences_are_superseded_and_audited(tmp_path: Path) -> None:
    store = SQLiteSessionMemoryStore(tmp_path / "memory.sqlite3", tmp_path / "project")
    await store.append_turn("one", "以后请始终使用中文回答。", "收到")
    await store.append_turn("two", "以后请始终使用英文回答。", "收到")

    preferences = await store.list_preferences()
    conflicts = await store.list_conflicts()

    assert [(item.value, item.active) for item in preferences] == [
        ("en", True),
        ("zh", False),
    ]
    assert len(conflicts) == 1
    assert conflicts[0].relation == "supersedes"
    assert conflicts[0].resolution == "newer_explicit_preference"


async def test_summary_chunk_records_compression_metadata(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    store = SQLiteSessionMemoryStore(
        database_path,
        tmp_path / "project",
        max_turns=1,
        medium_term_max_tokens=200,
    )
    await store.append_turn("session", "first request", "first outcome")
    await store.append_turn("session", "second request", "second outcome")

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT token_count, compression_stage, compression_reason,
                   input_token_count, dropped_items_json, compression_version, counter_id
            FROM memory_summary_chunks
            """
        ).fetchone()

    assert row is not None
    assert row[0] <= 200
    assert row[1] in {"C2", "C4"}
    assert row[2] == "older_turn_rollup"
    assert row[3] > 0
    assert row[4].startswith("[")
    assert row[5] == "deterministic-v1"
    assert row[6].startswith("estimated:")


async def test_v1_database_is_migrated_without_losing_memory(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE memory_sessions (
                session_id TEXT NOT NULL,
                project_key TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (session_id, project_key)
            );
            CREATE TABLE memory_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                project_key TEXT NOT NULL,
                user_message TEXT NOT NULL,
                assistant_message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE long_term_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                project_key TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                normalized_content TEXT NOT NULL,
                source_session_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (scope, project_key, normalized_content)
            );
            PRAGMA user_version = 1;
            """
        )

    store = SQLiteSessionMemoryStore(database_path, tmp_path / "project")
    await store.append_turn("session", "after migration", "still works", request_id="r1")
    messages = await store.get_messages("session")

    with sqlite3.connect(database_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        turn_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(memory_turns)")
        }
        chunk_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(memory_summary_chunks)")
        }

    assert version == 3
    assert "request_id" in turn_columns
    assert "compression_stage" in chunk_columns
    assert [message.content for message in messages] == ["after migration", "still works"]


async def test_newer_database_schema_is_rejected(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 999")

    store = SQLiteSessionMemoryStore(database_path, tmp_path / "project")

    with pytest.raises(MemoryStoreError, match="newer than supported"):
        await store.get_messages("session")


def test_memory_admin_api_reports_capabilities_and_forgets_preference() -> None:
    client = TestClient(create_app())
    with client.stream(
        "POST",
        "/api/v1/chat/completions",
        json={"session_id": "session", "message": "以后请始终使用中文回答。"},
    ) as response:
        "".join(response.iter_text())

    capabilities = client.get("/api/v1/memory/capabilities")
    preferences = client.get("/api/v1/memory/preferences")
    preference_id = preferences.json()[0]["id"]
    deleted = client.delete(f"/api/v1/memory/preferences/{preference_id}")

    assert capabilities.json() == {
        "backend": "sqlite",
        "layers": ["L1", "L2", "L3"],
        "persistent": True,
        "cross_process": True,
        "preference_management": True,
    }
    assert deleted.json() == {"status": "ok"}
    assert client.get("/api/v1/memory/preferences").json() == []


def test_memory_admin_api_exposes_conflict_audit() -> None:
    client = TestClient(create_app())
    for message in ("以后请始终使用中文回答。", "以后请始终使用英文回答。"):
        with client.stream(
            "POST",
            "/api/v1/chat/completions",
            json={"message": message},
        ) as response:
            "".join(response.iter_text())

    conflicts = client.get("/api/v1/memory/conflicts")

    assert conflicts.status_code == 200
    assert conflicts.json()[0]["relation"] == "supersedes"
