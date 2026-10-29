from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from nevis_search.app import create_app
from nevis_search.config import Settings
from nevis_search.database import Base, build_engine
from nevis_search.embeddings import Encoder


class FakeEncoder:
    """Small deterministic encoder for API and service tests."""

    dimensions = 128
    aliases = {
        "canine": "dog",
        "automobile": "car",
        "physician": "doctor",
    }

    def encode(self, texts: list[str]) -> np.ndarray:
        rows = []
        for text in texts:
            vector = np.zeros(self.dimensions, dtype=np.float32)
            for token in re.findall(r"\w+", text.casefold()):
                token = self.aliases.get(token, token)
                index = (
                    int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big")
                    % self.dimensions
                )
                vector[index] += 1
            norm = np.linalg.norm(vector)
            if norm:
                vector /= norm
            rows.append(vector)
        return np.asarray(rows, dtype=np.float32)


def make_api(
    database_path: Path,
    encoder: Encoder,
    *,
    api_key: str | None = None,
    semantic_threshold: float = 0.60,
) -> TestClient:
    database_url = f"sqlite:///{database_path}"
    engine = build_engine(database_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    settings = Settings(
        database_url=database_url,
        model_path=Path("assets/model"),
        api_key=SecretStr(api_key) if api_key else None,
        require_api_key=api_key is not None,
        vercel=False,
        semantic_threshold=semantic_threshold,
    )
    return TestClient(create_app(settings, encoder))


@pytest.fixture
def encoder() -> FakeEncoder:
    return FakeEncoder()


@pytest.fixture
def api(tmp_path: Path, encoder: FakeEncoder):
    with make_api(tmp_path / "search.sqlite3", encoder) as client:
        yield client


@pytest.fixture
def client_payload() -> dict[str, object]:
    return {
        "first_name": "Avery",
        "last_name": "North",
        "email": "avery.north@example.com",
        "description": "",
        "social_links": [],
    }
