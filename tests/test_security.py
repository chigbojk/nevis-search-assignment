from pathlib import Path

import pytest
from conftest import FakeEncoder, make_api
from pydantic import ValidationError

from nevis_search.config import Settings


def test_vercel_configuration_requires_key_and_durable_database(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    with pytest.raises(ValidationError, match="API_KEY"):
        Settings(_env_file=None, api_key=None, database_url="sqlite:///temporary.db")
    with pytest.raises(ValidationError, match="persistent PostgreSQL"):
        Settings(_env_file=None, api_key="a" * 40, database_url="sqlite:///temporary.db")
    settings = Settings(
        _env_file=None, api_key="a" * 40, database_url="postgresql://test@localhost/nevis"
    )
    assert settings.vercel


def test_request_body_limit_counts_received_bytes_without_content_length(
    tmp_path: Path, encoder: FakeEncoder
):
    with make_api(tmp_path / "body.sqlite3", encoder) as api:
        response = api.post(
            "/clients",
            content=iter([b"a" * 200_000] * 3),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json() == {"detail": "Request body exceeds 512 KiB"}


def test_document_and_social_url_validation(api):
    payload = {"first_name": "Amy", "last_name": "Chen", "email": "amy@example.com"}
    assert (
        api.post("/clients", json={**payload, "social_links": ["javascript:alert(1)"]}).status_code
        == 422
    )
    client_id = api.post("/clients", json=payload).json()["id"]
    for document in (
        {"title": "   ", "content": "Text"},
        {"title": "Notes", "content": "   "},
        {"title": "Notes", "content": "a" * 100_001},
    ):
        assert api.post(f"/clients/{client_id}/documents", json=document).status_code == 422
