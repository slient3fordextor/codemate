from fastapi.testclient import TestClient

from app.main import create_app


def test_chat_completion_streams_mock_response() -> None:
    client = TestClient(create_app())

    with client.stream(
        "POST",
        "/api/v1/chat/completions",
        json={"message": "解释当前架构", "stream": True},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: message.start" in body
    assert "event: message.delta" in body
    assert '"content": "Mock "' in body
    assert '"content": "response: "' in body
    assert "event: message.done" in body


def test_chat_completion_accepts_message_protocol_context() -> None:
    client = TestClient(create_app())

    with client.stream(
        "POST",
        "/api/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "总结"}],
            "current_file": "app/main.py",
            "selected_text": "create_app()",
        },
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: usage.update" in body


def test_chat_completion_uses_l1_session_memory() -> None:
    client = TestClient(create_app())

    with client.stream(
        "POST",
        "/api/v1/chat/completions",
        json={"session_id": "ses_test_memory", "message": "第一轮"},
    ) as response:
        first_body = "".join(response.iter_text())

    with client.stream(
        "POST",
        "/api/v1/chat/completions",
        json={"session_id": "ses_test_memory", "message": "第二轮"},
    ) as response:
        second_body = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"session_id": "ses_test_memory"' in first_body
    assert '"input_tokens": 5' in second_body
    assert '"memory_tokens": 0' not in second_body


def test_chat_completion_messages_do_not_add_l1_session_memory() -> None:
    client = TestClient(create_app())

    with client.stream(
        "POST",
        "/api/v1/chat/completions",
        json={"session_id": "ses_messages_mode", "message": "第一轮"},
    ) as response:
        "".join(response.iter_text())

    with client.stream(
        "POST",
        "/api/v1/chat/completions",
        json={
            "session_id": "ses_messages_mode",
            "messages": [{"role": "user", "content": "显式上下文"}],
        },
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"input_tokens": 1' in body


def test_chat_completion_requires_message_or_messages() -> None:
    client = TestClient(create_app())

    response = client.post("/api/v1/chat/completions", json={"stream": True})

    assert response.status_code == 422


def test_chat_completion_rejects_unsafe_session_id() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/chat/completions",
        json={"session_id": "../outside", "message": "hello"},
    )

    assert response.status_code == 422
