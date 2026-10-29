from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Content = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000)]


class ClientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    first_name: Name
    last_name: Name
    email: EmailStr = Field(max_length=254)
    description: str = Field(default="", max_length=5_000)
    social_links: list[HttpUrl] = Field(default_factory=list, max_length=10)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: str) -> str:
        return email.casefold()


class ClientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    first_name: str
    last_name: str
    email: str
    description: str
    social_links: list[str]


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    content: Content


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    title: str
    content: str
    created_at: datetime
    summary: str
    summary_method: Literal["extractive"] = "extractive"

    @field_validator("created_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        # SQLite drops the offset; all writes are UTC.
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ClientResult(BaseModel):
    type: Literal["client"] = "client"
    score: float
    match_type: Literal["lexical"] = "lexical"
    client: ClientRead


class DocumentResult(BaseModel):
    type: Literal["document"] = "document"
    score: float
    match_type: Literal["lexical", "semantic", "concept", "hybrid"]
    matched_text: str
    document: DocumentRead


SearchResult = Annotated[ClientResult | DocumentResult, Field(discriminator="type")]


class ErrorResponse(BaseModel):
    detail: str
