import pytest

from app.services.session_memory import InMemorySessionMemoryStore, SessionMemoryPolicy


def test_session_memory_policy_requires_positive_limits() -> None:
    with pytest.raises(ValueError, match="max_turns"):
        SessionMemoryPolicy(max_turns=0)

    with pytest.raises(ValueError, match="max_sessions"):
        SessionMemoryPolicy(max_sessions=0)


def test_in_memory_session_memory_returns_recent_turns_only() -> None:
    store = InMemorySessionMemoryStore(max_turns=2, max_sessions=10)

    store.append_turn("ses_1", "user 1", "assistant 1")
    store.append_turn("ses_1", "user 2", "assistant 2")
    store.append_turn("ses_1", "user 3", "assistant 3")

    messages = store.get_messages("ses_1")

    assert [message.content for message in messages] == [
        "user 2",
        "assistant 2",
        "user 3",
        "assistant 3",
    ]


def test_in_memory_session_memory_evicts_least_recently_used_session() -> None:
    store = InMemorySessionMemoryStore(max_turns=10, max_sessions=2)

    store.append_turn("ses_1", "user 1", "assistant 1")
    store.append_turn("ses_2", "user 2", "assistant 2")
    store.get_messages("ses_1")
    store.append_turn("ses_3", "user 3", "assistant 3")

    assert store.get_messages("ses_1")
    assert store.get_messages("ses_2") == []
    assert store.get_messages("ses_3")


def test_in_memory_session_memory_returns_copied_messages() -> None:
    store = InMemorySessionMemoryStore()
    store.append_turn("ses_1", "user", "assistant")

    messages = store.get_messages("ses_1")
    messages[0].content = "changed"

    assert store.get_messages("ses_1")[0].content == "user"
