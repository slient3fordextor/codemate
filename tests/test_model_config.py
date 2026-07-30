from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_model_config_can_be_saved_to_env_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(create_app())

    response = client.put(
        "/api/v1/model-config",
        json={
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "name": "example-model",
            "api_key": "secret-key",
            "note": "main coding model",
            "api_key_mode": "replace",
            "timeout_seconds": 45,
            "max_retries": 3,
            "context_window": 128000,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "provider": "openai_compatible",
        "base_url": "https://api.example.com/v1",
        "name": "example-model",
        "api_key_set": True,
        "note": "main coding model",
        "timeout_seconds": 45.0,
        "max_retries": 3,
        "context_window": 128000,
    }

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "MODEL_PROVIDER=openai_compatible" in env_text
    assert "MODEL_BASE_URL=https://api.example.com/v1" in env_text
    assert "MODEL_NAME=example-model" in env_text
    assert "MODEL_API_KEY=secret-key" in env_text
    assert 'MODEL_NOTE="main coding model"' in env_text
    assert "MODEL_TIMEOUT_SECONDS=45.0" in env_text
    assert "MODEL_MAX_RETRIES=3" in env_text
    assert "MODEL_CONTEXT_WINDOW=128000" in env_text


def test_model_config_preserves_existing_api_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "MODEL_PROVIDER=openai_compatible",
                "MODEL_BASE_URL=https://api.example.com/v1",
                "MODEL_NAME=old-model",
                "MODEL_API_KEY=existing-secret",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    client = TestClient(create_app())

    response = client.put(
        "/api/v1/model-config",
        json={
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "name": "new-model",
            "note": "preserved key",
            "api_key_mode": "preserve",
            "timeout_seconds": 60,
            "max_retries": 2,
        },
    )

    assert response.status_code == 200
    assert response.json()["api_key_set"] is True
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "MODEL_API_KEY=existing-secret" in env_text
    assert "MODEL_NAME=new-model" in env_text
    assert 'MODEL_NOTE="preserved key"' in env_text


def test_model_config_can_clear_api_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MODEL_API_KEY=existing-secret\n", encoding="utf-8")
    client = TestClient(create_app())

    response = client.put(
        "/api/v1/model-config",
        json={
            "provider": "mock",
            "base_url": "",
            "name": "mock-model",
            "note": "",
            "api_key_mode": "clear",
            "timeout_seconds": 60,
            "max_retries": 2,
        },
    )

    assert response.status_code == 200
    assert response.json()["api_key_set"] is False
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "MODEL_API_KEY=" in env_text
