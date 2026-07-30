import asyncio
import multiprocessing
from pathlib import Path

import pytest

from app.services.persistent_memory import PreferenceExtractor, SQLiteSessionMemoryStore
from app.services.session_memory import InMemorySessionMemoryStore, SessionMemoryPolicy


def _append_turn_in_process(
    database_path: str,
    project_root: str,
    session_id: str,
    user_message: str,
) -> None:
    store = SQLiteSessionMemoryStore(Path(database_path), Path(project_root))
    asyncio.run(store.append_turn(session_id, user_message, f"answer for {user_message}"))


def test_session_memory_policy_requires_positive_limits() -> None:
    with pytest.raises(ValueError, match="max_turns"):
        SessionMemoryPolicy(max_turns=0)

    with pytest.raises(ValueError, match="max_sessions"):
        SessionMemoryPolicy(max_sessions=0)


async def test_in_memory_session_memory_returns_recent_turns_only() -> None:
    store = InMemorySessionMemoryStore(max_turns=2, max_sessions=10)

    await store.append_turn("ses_1", "user 1", "assistant 1")
    await store.append_turn("ses_1", "user 2", "assistant 2")
    await store.append_turn("ses_1", "user 3", "assistant 3")

    messages = await store.get_messages("ses_1")

    assert [message.content for message in messages] == [
        "user 2",
        "assistant 2",
        "user 3",
        "assistant 3",
    ]


async def test_in_memory_session_memory_evicts_least_recently_used_session() -> None:
    store = InMemorySessionMemoryStore(max_turns=10, max_sessions=2)

    await store.append_turn("ses_1", "user 1", "assistant 1")
    await store.append_turn("ses_2", "user 2", "assistant 2")
    await store.get_messages("ses_1")
    await store.append_turn("ses_3", "user 3", "assistant 3")

    assert await store.get_messages("ses_1")
    assert await store.get_messages("ses_2") == []
    assert await store.get_messages("ses_3")


async def test_in_memory_session_memory_returns_copied_messages() -> None:
    store = InMemorySessionMemoryStore()
    await store.append_turn("ses_1", "user", "assistant")

    messages = await store.get_messages("ses_1")
    messages[0].content = "changed"

    assert (await store.get_messages("ses_1"))[0].content == "user"


async def test_sqlite_memory_recovers_recent_turns_after_new_store_instance(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "shared" / "memory.sqlite3"
    project_root = tmp_path / "project-a"
    first_store = SQLiteSessionMemoryStore(database_path, project_root)
    await first_store.append_turn("ses_1", "第一轮", "第一个回答")

    restarted_store = SQLiteSessionMemoryStore(database_path, project_root)

    assert [message.content for message in await restarted_store.get_messages("ses_1")] == [
        "第一轮",
        "第一个回答",
    ]


async def test_sqlite_memory_rolls_older_turns_into_medium_term_summary(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionMemoryStore(
        tmp_path / "memory.sqlite3",
        tmp_path / "project",
        max_turns=2,
        medium_term_max_tokens=1_000,
    )
    await store.append_turn("ses_1", "user 1", "assistant 1")
    await store.append_turn("ses_1", "user 2", "assistant 2")
    await store.append_turn("ses_1", "user 3", "assistant 3")

    messages = await store.get_messages("ses_1")

    assert messages[0].role == "user"
    assert messages[0].name == "memory_context"
    assert "Historical conversation summary" in messages[0].content
    assert "Request: user 1" in messages[0].content
    assert [message.content for message in messages[1:]] == [
        "user 2",
        "assistant 2",
        "user 3",
        "assistant 3",
    ]


async def test_sqlite_medium_term_summary_respects_strict_token_limit(tmp_path: Path) -> None:
    store = SQLiteSessionMemoryStore(
        tmp_path / "memory.sqlite3",
        tmp_path / "project",
        max_turns=1,
        medium_term_max_tokens=40,
    )
    await store.append_turn("ses_1", "u" * 100, "a" * 100)
    await store.append_turn("ses_1", "recent", "answer")

    summary_message = (await store.get_messages("ses_1"))[0]
    summary = summary_message.content.split("\n", maxsplit=1)[1]

    assert len(summary.encode("utf-8")) / 3 <= 40


async def test_sqlite_memory_shares_global_preferences_without_leaking_project_rules(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    project_a = SQLiteSessionMemoryStore(database_path, tmp_path / "project-a")
    project_b = SQLiteSessionMemoryStore(database_path, tmp_path / "project-b")
    await project_a.append_turn(
        "ses_a",
        "以后请始终使用中文回答。本项目统一使用 pytest 测试。",
        "收到",
    )

    project_a_context = await project_a.get_messages("new_session")
    project_b_context = await project_b.get_messages("new_session")

    assert "以后请始终使用中文回答" in project_a_context[0].content
    assert "本项目统一使用 pytest 测试" in project_a_context[0].content
    assert "以后请始终使用中文回答" in project_b_context[0].content
    assert "本项目统一使用 pytest 测试" not in project_b_context[0].content


async def test_two_sqlite_store_instances_observe_the_same_session(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    project_root = tmp_path / "project"
    first_worker = SQLiteSessionMemoryStore(database_path, project_root)
    second_worker = SQLiteSessionMemoryStore(database_path, project_root)

    await first_worker.append_turn("ses_shared", "user 1", "assistant 1")
    await second_worker.append_turn("ses_shared", "user 2", "assistant 2")

    assert [message.content for message in await first_worker.get_messages("ses_shared")] == [
        "user 1",
        "assistant 1",
        "user 2",
        "assistant 2",
    ]


async def test_sqlite_memory_accepts_concurrent_writes_from_multiple_processes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "memory.sqlite3"
    project_root = tmp_path / "project"
    process_context = multiprocessing.get_context("spawn")
    processes = [
        process_context.Process(
            target=_append_turn_in_process,
            args=(
                str(database_path),
                str(project_root),
                "ses_shared",
                f"user {index}",
            ),
        )
        for index in range(2)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)

    assert [process.exitcode for process in processes] == [0, 0]
    messages = await SQLiteSessionMemoryStore(database_path, project_root).get_messages(
        "ses_shared"
    )
    assert {message.content for message in messages if message.role == "user"} == {
        "user 0",
        "user 1",
    }


def test_preference_extractor_requires_stable_intent_and_filters_secrets() -> None:
    extractor = PreferenceExtractor()

    preferences = extractor.extract(
        "帮我修复这个接口。以后请始终使用中文回答。"
        "本项目禁止直接提交生成文件。默认 token 是 sk-sensitive。"
    )

    assert [(item.scope, item.content) for item in preferences] == [
        ("global", "以后请始终使用中文回答。"),
        ("project", "本项目禁止直接提交生成文件。"),
    ]
