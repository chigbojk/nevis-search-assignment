from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeEncoder, make_api

from nevis_search.embeddings import LocalEncoder

MODEL_PATH = Path(__file__).resolve().parents[1] / "assets" / "model"


@pytest.mark.semantic
@pytest.mark.skipif(
    not (MODEL_PATH / "model.safetensors").is_file(), reason="local model is absent"
)
def test_real_encoder_retrieves_retirement_review_semantically(tmp_path: Path):
    encoder = LocalEncoder(str(MODEL_PATH))
    with make_api(tmp_path / "semantic.sqlite3", encoder) as api:
        owner = api.post(
            "/clients", json={"first_name": "Rae", "last_name": "Gray", "email": "rae@example.com"}
        ).json()
        pension = api.post(
            f"/clients/{owner['id']}/documents",
            json={
                "title": "Pension review",
                "content": (
                    "Retirement savings and pension contribution projections for next twenty years."
                ),
            },
        )
        assert pension.status_code == 201, pension.text
        api.post(
            f"/clients/{owner['id']}/documents",
            json={
                "title": "Kitchen notes",
                "content": "A recipe for baking bread with olive oil and fresh herbs.",
            },
        )

        response = api.get("/search", params={"q": "retirement planning"})
        assert response.status_code == 200
        [result] = response.json()
        assert result["document"]["id"] == pension.json()["id"]
        assert result["match_type"] == "semantic"


@pytest.mark.semantic
@pytest.mark.skipif(
    not (MODEL_PATH / "model.safetensors").is_file(), reason="local model is absent"
)
def test_real_model_corpus_covers_address_and_income_concepts_without_gibberish(
    tmp_path: Path,
):
    encoder = LocalEncoder(str(MODEL_PATH))
    with make_api(tmp_path / "concepts.sqlite3", encoder) as api:
        owner = api.post(
            "/clients", json={"first_name": "Rae", "last_name": "Gray", "email": "rae@example.com"}
        ).json()
        address = api.post(
            f"/clients/{owner['id']}/documents",
            json={"title": "Utility bill", "content": "Residential service address statement."},
        )
        income = api.post(
            f"/clients/{owner['id']}/documents",
            json={"title": "Payslip", "content": "Monthly salary statement for the employee."},
        )
        assert address.status_code == income.status_code == 201

        address_results = api.get("/search", params={"q": "address proof"}).json()
        address_documents = [
            item["document"]["id"] for item in address_results if item["type"] == "document"
        ]
        assert address.json()["id"] in address_documents

        income_results = api.get("/search", params={"q": "income verification"}).json()
        income_documents = [
            item["document"]["id"] for item in income_results if item["type"] == "document"
        ]
        assert income.json()["id"] in income_documents

        gibberish = api.get("/search", params={"q": "zzqvplkxxqvplk"})
        assert gibberish.status_code == 200
        assert gibberish.json() == []


def test_local_fake_encoder_aliases_are_deterministic():
    encoder = FakeEncoder()
    canine, dog = encoder.encode(["canine", "dog"])
    assert (canine == dog).all()
