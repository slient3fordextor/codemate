from fastapi.testclient import TestClient

from app.main import create_app


def test_health_check() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-request-id"]


def test_versioned_health_check() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-request-id"]


def test_version() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/version")

    assert response.status_code == 200
    assert response.json() == {
        "name": "CodeMate",
        "version": "0.1.0",
        "api_version": "v1",
    }
