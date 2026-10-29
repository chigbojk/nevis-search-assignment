from collections.abc import Iterator
from contextlib import asynccontextmanager
from hmac import compare_digest
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nevis_search import service
from nevis_search.config import Settings
from nevis_search.database import build_engine
from nevis_search.embeddings import Encoder, LocalEncoder
from nevis_search.schemas import (
    ClientCreate,
    ClientRead,
    DocumentCreate,
    DocumentRead,
    ErrorResponse,
    SearchResult,
)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class BodySizeLimit:
    """Enforce the limit on bytes received, including chunked requests."""

    def __init__(self, app: ASGIApp, max_bytes: int = 512 * 1024):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                await JSONResponse({"detail": "Request body exceeds 512 KiB"}, 413)(
                    scope, receive, send
                )
                return
            if not message.get("more_body", False):
                break
        consumed = False

        async def replay() -> Message:
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None, encoder: Encoder | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = build_engine(settings.database_url)
        try:
            app.state.engine = engine
            app.state.encoder = encoder or LocalEncoder(str(settings.model_path))
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Nevis Search API",
        version="1.0.0",
        description=(
            "Search client names, emails and descriptions, and document contents. "
            "Document retrieval combines text matching with local semantic embeddings. "
            "For protected deployments, enter the X-API-Key using Authorize."
        ),
        lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": False},
    )
    app.add_middleware(BodySizeLimit)

    def session(request: Request) -> Iterator[Session]:
        with Session(request.app.state.engine, expire_on_commit=False) as db:
            yield db

    def require_key(key: Annotated[str | None, Depends(api_key_header)]) -> None:
        expected = settings.api_key.get_secret_value() if settings.api_key else None
        if expected and (key is None or not compare_digest(key.encode(), expected.encode())):
            raise HTTPException(
                401, "Invalid or missing API key", headers={"WWW-Authenticate": "ApiKey"}
            )

    Db = Annotated[Session, Depends(session)]
    protected = [Depends(require_key)]
    errors = {401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}

    @app.exception_handler(service.NotFound)
    async def not_found(_request: Request, exc: service.NotFound):
        return JSONResponse({"detail": str(exc)}, 404)

    @app.exception_handler(service.DuplicateEmail)
    async def duplicate_email(_request: Request, _exc: service.DuplicateEmail):
        return JSONResponse({"detail": "A client with this email already exists"}, 409)

    @app.exception_handler(SQLAlchemyError)
    async def database_unavailable(_request: Request, _exc: SQLAlchemyError):
        # Never return a driver exception: it may contain SQL parameters or credentials.
        return JSONResponse({"detail": "Database unavailable"}, 503)

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/docs")

    @app.get("/health", tags=["Operations"], responses={503: {"model": ErrorResponse}})
    def health(db: Db):
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.post(
        "/clients",
        status_code=201,
        response_model=ClientRead,
        tags=["Clients"],
        dependencies=protected,
        responses={**errors, 409: {"model": ErrorResponse}},
    )
    def add_client(data: ClientCreate, response: Response, db: Db):
        client = service.create_client(db, data)
        response.headers["Location"] = f"/clients/{client.id}"
        return client

    @app.get(
        "/clients/{id}",
        response_model=ClientRead,
        tags=["Clients"],
        dependencies=protected,
        responses={**errors, 404: {"model": ErrorResponse}},
    )
    def read_client(id: UUID, db: Db):
        return service.get_client(db, str(id))

    @app.post(
        "/clients/{id}/documents",
        status_code=201,
        response_model=DocumentRead,
        tags=["Documents"],
        dependencies=protected,
        responses={**errors, 404: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
    )
    def add_document(id: UUID, data: DocumentCreate, response: Response, request: Request, db: Db):
        document = service.create_document(db, str(id), data, request.app.state.encoder)
        response.headers["Location"] = f"/documents/{document.id}"
        return document

    @app.get(
        "/documents/{id}",
        response_model=DocumentRead,
        tags=["Documents"],
        dependencies=protected,
        responses={**errors, 404: {"model": ErrorResponse}},
    )
    def read_document(id: UUID, db: Db):
        return service.get_document(db, str(id))

    @app.get(
        "/search",
        response_model=list[SearchResult],
        tags=["Search"],
        dependencies=protected,
        responses={**errors, 404: {"model": ErrorResponse}},
    )
    def find(
        request: Request,
        db: Db,
        q: Annotated[str, Query(min_length=1, max_length=500)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
        client_id: UUID | None = None,
    ):
        query = q.strip()
        if not query or not any(character.isalnum() for character in query):
            raise HTTPException(422, "Search query must contain a letter or number")
        return service.search(
            db,
            query,
            request.app.state.encoder,
            settings.semantic_threshold,
            limit,
            offset,
            str(client_id) if client_id else None,
        )

    return app
