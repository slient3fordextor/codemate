from fastapi.testclient import TestClient

from app.main import create_app


def test_web_index_serves_cli_chat_page() -> None:
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "CodeMate" in response.text
    assert "/static/app.js" in response.text
