from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nevis_search.concepts import matches_concept, related_terms
from nevis_search.embeddings import Encoder
from nevis_search.models import Client, Document, DocumentChunk
from nevis_search.schemas import (
    ClientCreate,
    ClientRead,
    ClientResult,
    DocumentCreate,
    DocumentRead,
    DocumentResult,
)
from nevis_search.text import chunk_text, lexical_score, summarize


class DuplicateEmail(Exception):
    pass


class NotFound(Exception):
    pass


def get_client(session: Session, client_id: str) -> Client:
    client = session.get(Client, client_id)
    if client is None:
        raise NotFound("Client not found")
    return client


def get_document(session: Session, document_id: str) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise NotFound("Document not found")
    return document


def create_client(session: Session, data: ClientCreate) -> Client:
    client = Client(**data.model_dump(mode="json"))
    session.add(client)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        # Only map the known uniqueness conflict; don't hide unrelated storage errors.
        if session.scalar(select(Client.id).where(Client.email == str(data.email))):
            raise DuplicateEmail from None
        raise
    session.refresh(client)
    return client


def create_document(
    session: Session, client_id: str, data: DocumentCreate, encoder: Encoder
) -> Document:
    get_client(session, client_id)
    chunks = chunk_text(data.content)
    vectors = encoder.encode([f"{data.title}\n{text}" for text in chunks])
    document = Document(
        client_id=client_id,
        **data.model_dump(),
        summary=summarize(data.content, encoder),
    )
    session.add(document)
    session.flush()
    session.add_all(
        DocumentChunk(document_id=document.id, position=i, text=text, embedding=vector.tolist())
        for i, (text, vector) in enumerate(zip(chunks, vectors, strict=True))
    )
    # Document and all embeddings become visible together, or none do.
    session.commit()
    session.refresh(document)
    return document


@dataclass(frozen=True)
class ChunkMatch:
    score: float
    lexical: float
    semantic: float
    concept: bool
    text: str


def search(
    session: Session,
    query: str,
    encoder: Encoder,
    threshold: float,
    limit: int,
    offset: int,
    client_id: str | None = None,
) -> list[ClientResult | DocumentResult]:
    results: list[ClientResult | DocumentResult] = []
    clients = select(Client).order_by(Client.id)
    if client_id is not None:
        get_client(session, client_id)
        clients = clients.where(Client.id == client_id)
    for client in session.scalars(clients):
        score = lexical_score(
            query,
            f"{client.first_name} {client.last_name} {client.email} {client.description}",
        )
        if score:
            results.append(ClientResult(score=score, client=ClientRead.model_validate(client)))

    query_vector = encoder.encode([query])[0]
    concepts = related_terms(query)
    candidates = select(
        DocumentChunk.document_id,
        DocumentChunk.text,
        DocumentChunk.embedding,
        Document.title,
    ).join(Document, Document.id == DocumentChunk.document_id)
    if client_id is not None:
        candidates = candidates.where(Document.client_id == client_id)
    candidates = candidates.order_by(DocumentChunk.document_id, DocumentChunk.position)
    best_matches: dict[str, ChunkMatch] = {}
    for document_id, text, embedding, title in session.execute(candidates).yield_per(256):
        lexical = lexical_score(query, f"{title}\n{text}")
        concept = matches_concept(concepts, f"{title}\n{text}")
        semantic = float(np.clip(np.dot(query_vector, embedding), 0, 1))
        if not lexical and not concept and semantic < threshold:
            continue
        # Lexical matches stay ahead of semantic-only matches. Similarity breaks
        # lexical ties without pretending this score is a calibrated probability.
        textual_score = max(lexical, 0.9 if concept else 0.0)
        score = 0.75 * textual_score + 0.25 * semantic if textual_score else 0.70 * semantic
        previous = best_matches.get(document_id)
        if previous is None or score > previous.score:
            best_matches[document_id] = ChunkMatch(score, lexical, semantic, concept, text)

    if best_matches:
        documents = session.scalars(select(Document).where(Document.id.in_(best_matches)))
        for document in documents:
            match = best_matches[document.id]
            match_type = (
                "hybrid"
                if (match.lexical or match.concept) and match.semantic >= threshold
                else "lexical"
                if match.lexical
                else "concept"
                if match.concept
                else "semantic"
            )
            results.append(
                DocumentResult(
                    score=round(match.score, 6),
                    match_type=match_type,
                    matched_text=match.text,
                    document=DocumentRead.model_validate(document),
                )
            )

    results.sort(
        key=lambda result: (
            -result.score,
            result.type,
            result.client.id if isinstance(result, ClientResult) else result.document.id,
        )
    )
    return results[offset : offset + limit]
