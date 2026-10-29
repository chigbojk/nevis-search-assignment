from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import pytest
from conftest import FakeEncoder, make_api
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

from nevis_search.models import Client, Document, DocumentChunk


def create_client(
    api, *, first_name="Avery", last_name="North", email="avery@example.com", description=""
):
    response = api.post(
        "/clients",
        json={
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "description": description,
            "social_links": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_document(api, client_id: str, *, title: str, content: str):
    response = api.post(
        f"/clients/{client_id}/documents", json={"title": title, "content": content}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_client_email_is_normalized_and_duplicate_conflicts_ignore_case(api, client_payload):
    client_payload["email"] = "Avery.North@Example.COM"
    created = api.post("/clients", json=client_payload)

    assert created.status_code == 201
    assert created.json()["email"] == "avery.north@example.com"
    assert created.headers["location"] == f"/clients/{created.json()['id']}"

    duplicate = {**client_payload, "email": "AVERY.NORTH@example.com"}
    response = api.post("/clients", json=duplicate)
    assert response.status_code == 409
    assert response.json() == {"detail": "A client with this email already exists"}


@pytest.mark.parametrize(
    "changes",
    [
        {"first_name": "   "},
        {"email": "not-an-email"},
        {"unexpected": True},
        {"social_links": [f"https://example.com/{i}" for i in range(11)]},
    ],
)
def test_client_create_rejects_invalid_or_unexpected_fields(api, client_payload, changes):
    response = api.post("/clients", json={**client_payload, **changes})
    assert response.status_code == 422


def test_document_for_missing_client_returns_404_without_creating_rows(api):
    response = api.post(
        f"/clients/{uuid4()}/documents",
        json={"title": "Proof", "content": "A utility bill"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Client not found"}
    with api.app.state.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Document)) == 0
        assert connection.scalar(select(func.count()).select_from(DocumentChunk)) == 0


def test_document_and_chunks_roll_back_together_when_chunk_insert_fails(api, client_payload):
    owner = create_client(api)

    def fail_chunk_insert(*_args, **_kwargs):
        raise IntegrityError("forced chunk failure", {}, RuntimeError("test"))

    event.listen(DocumentChunk, "before_insert", fail_chunk_insert)
    try:
        response = api.post(
            f"/clients/{owner['id']}/documents",
            json={"title": "Atomic document", "content": "one short source sentence."},
        )
    finally:
        event.remove(DocumentChunk, "before_insert", fail_chunk_insert)

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
    with api.app.state.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Document)) == 0
        assert connection.scalar(select(func.count()).select_from(DocumentChunk)) == 0


@pytest.mark.parametrize(
    ("query", "first_name", "last_name", "email", "description"),
    [
        ("Mira", "Mira", "Chen", "mira@example.com", ""),
        ("harbor@example.com", "Lena", "West", "harbor@example.com", ""),
        ("neuroscience", "Tariq", "Vale", "tariq@example.com", "Works in neuroscience."),
    ],
)
def test_search_finds_client_name_email_and_description(
    api, query, first_name, last_name, email, description
):
    created = create_client(
        api,
        first_name=first_name,
        last_name=last_name,
        email=email,
        description=description,
    )

    response = api.get("/search", params={"q": query})
    assert response.status_code == 200
    assert response.json()[0]["type"] == "client"
    assert response.json()[0]["match_type"] == "lexical"
    assert response.json()[0]["client"]["id"] == created["id"]


def test_fake_encoder_drives_semantic_only_document_match(api):
    owner = create_client(api)
    create_document(api, owner["id"], title="Animal", content="dog")

    response = api.get("/search", params={"q": "canine"})
    assert response.status_code == 200
    [result] = response.json()
    assert result["type"] == "document"
    assert result["match_type"] == "semantic"
    assert result["matched_text"] == "dog"


def test_concept_retrieval_word_boundaries_and_qualified_query_guard(api):
    owner = create_client(api)
    create_document(api, owner["id"], title="Address record", content="A recent utility bill.")
    create_document(
        api, owner["id"], title="No proof", content="An account has notutility billboard notes."
    )

    direct = api.get("/search", params={"q": "address proof"})
    assert direct.status_code == 200
    matches = [item for item in direct.json() if item["type"] == "document"]
    assert len(matches) == 1
    assert matches[0]["match_type"] in {"concept", "hybrid"}
    assert matches[0]["document"]["content"] == "A recent utility bill."

    qualified = api.get("/search", params={"q": "address proof for Alice"})
    assert qualified.status_code == 200
    assert qualified.json() == []


def test_document_summary_is_extractive_and_identifies_method(api):
    owner = create_client(api)
    source = (
        "The annual report covers staffing. "
        "It records revenue and operating costs. "
        "The final section describes next year's service plan. "
    ) * 10
    document = create_document(api, owner["id"], title="Annual report", content=source)

    assert document["summary_method"] == "extractive"
    assert 0 < len(document["summary"]) <= 600
    source_sentences = [
        sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+|\n+", source) if sentence.strip()
    ]
    summary_sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", document["summary"])
        if sentence.strip()
    ]
    cursor = 0
    for sentence in summary_sentences:
        cursor += source_sentences[cursor:].index(sentence) + 1
    assert api.get(f"/documents/{document['id']}").json()["summary"] == document["summary"]


def test_search_scope_limit_offset_empty_and_invalid_inputs(api):
    owners = [
        create_client(
            api,
            first_name=f"Owner{i}",
            last_name="Scope",
            email=f"owner{i}@example.com",
            description="sharedscope marker",
        )
        for i in range(3)
    ]
    create_document(api, owners[0]["id"], title="Scope file", content="sharedscope marker")

    all_results = api.get("/search", params={"q": "sharedscope"}).json()
    assert len(all_results) == 4
    assert api.get("/search", params={"q": "sharedscope", "limit": 2}).json() == all_results[:2]
    assert api.get("/search", params={"q": "sharedscope", "offset": 2}).json() == all_results[2:]
    scoped = api.get("/search", params={"q": "sharedscope", "client_id": owners[0]["id"]}).json()
    assert {item["type"] for item in scoped} == {"client", "document"}
    assert len(scoped) == 2

    missing_scope = api.get("/search", params={"q": "sharedscope", "client_id": str(uuid4())})
    assert missing_scope.status_code == 404
    assert missing_scope.json() == {"detail": "Client not found"}

    for params in (
        {"q": "   "},
        {"q": "---"},
        {"q": "x", "limit": 0},
        {"q": "x", "limit": 101},
        {"q": "x", "offset": -1},
        {"q": "x", "offset": 10001},
        {"q": "x", "client_id": "not-a-uuid"},
    ):
        assert api.get("/search", params=params).status_code == 422


def test_search_ties_are_stable_and_duplicate_chunk_matches_collapse(api):
    first = create_client(api, first_name="First", email="first@example.com")
    second = create_client(api, first_name="Second", email="second@example.com")
    content = " ".join(["tailneedle"] * 170 + ["ordinary"] * 20 + ["tailneedle"] * 170)
    first_doc = create_document(api, first["id"], title="Notes", content=content)
    second_doc = create_document(api, second["id"], title="Notes", content=content)

    results = api.get("/search", params={"q": "tailneedle"}).json()
    documents = [item for item in results if item["type"] == "document"]
    assert [item["document"]["id"] for item in documents] == sorted(
        [first_doc["id"], second_doc["id"]]
    )
    assert all(item["document"]["id"] in {first_doc["id"], second_doc["id"]} for item in documents)
    assert len(documents) == 2


def test_api_key_protects_every_data_endpoint(tmp_path: Path, encoder: FakeEncoder):
    key = "k" * 40
    with make_api(tmp_path / "protected.sqlite3", encoder, api_key=key) as api:
        owner_id = str(uuid4())
        document_id = str(uuid4())
        requests = [
            (
                "post",
                "/clients",
                {"json": {"first_name": "A", "last_name": "B", "email": "a@b.com"}},
            ),
            ("get", f"/clients/{owner_id}", {}),
            ("post", f"/clients/{owner_id}/documents", {"json": {"title": "A", "content": "B"}}),
            ("get", f"/documents/{document_id}", {}),
            ("get", "/search", {"params": {"q": "anything"}}),
        ]
        for method, path, kwargs in requests:
            assert getattr(api, method)(path, **kwargs).status_code == 401
        assert api.get("/health").status_code == 200
        assert (
            api.get("/search", params={"q": "anything"}, headers={"X-API-Key": key}).status_code
            == 200
        )


def test_sql_injection_like_search_is_inert(api):
    create_client(api, description="ordinary profile")

    response = api.get("/search", params={"q": "' OR 1=1 --"})
    assert response.status_code == 200
    assert response.json() == []
    with api.app.state.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Client)) == 1
