# Nevis Search API

A Python API for searching advisor clients and their documents. FastAPI provides the
HTTP layer and OpenAPI documentation; SQLAlchemy handles PostgreSQL and local SQLite.
Document search combines lexical matching, a small KYC vocabulary, and local embeddings.
No LLM account, API key, GPU, or external inference service is needed.

## Run with Docker

```sh
docker compose up --build -d
curl http://localhost:8000/health
```

Open [Swagger UI](http://localhost:8000/docs). Compose starts PostgreSQL, waits for it to
be healthy, applies the migration, and starts the API. The first build downloads the
pinned embedding model (about 30 MB). After that, inference works without network access.
The database uses a named Docker volume; restarting or rebuilding the API preserves data.
The API binds to localhost and PostgreSQL is not exposed to the host.

```sh
docker compose logs api
docker compose down          # preserves the database
```

## Run locally

Requires Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --frozen
cp .env.example .env
uv run python scripts/download_model.py
uv run alembic upgrade head
uv run uvicorn main:app --reload --no-access-log
```

The local default is SQLite in `data/search.db`. To use PostgreSQL, set `DATABASE_URL`
to `postgresql+psycopg://user:password@host/database` before running migrations and the API.
Schema changes are explicit Alembic migrations; the web process never creates tables.

## Try it

Load the synthetic demo data after starting either setup:

```sh
uv run python scripts/seed_demo.py
curl 'http://localhost:8000/search?q=NevisWealth'
curl 'http://localhost:8000/search?q=address%20proof'
curl 'http://localhost:8000/search?q=retirement%20planning'
curl 'http://localhost:8000/search?q=salary%20evidence'
```

The seed script can be run repeatedly without duplicating its example records. It creates
John Doe and Maya Patel, plus utility-bill, pension, passport and payslip documents.

Create your own client and document:

```sh
curl -i http://localhost:8000/clients \
  -H 'Content-Type: application/json' \
  -d '{"first_name":"Alex","last_name":"Chen","email":"alex@example.com"}'

# Replace CLIENT_ID with the id returned above.
curl -i http://localhost:8000/clients/CLIENT_ID/documents \
  -H 'Content-Type: application/json' \
  -d '{"title":"Water bill","content":"Monthly water bill for Alex Chen at 12 Elm Road."}'
```

`GET /search?q=NevisWealth` returns a JSON array containing:

```json
[
  {
    "type": "client",
    "score": 1.0,
    "match_type": "lexical",
    "client": {
      "id": "e61c101c-a542-4b9c-95dc-07e487323187",
      "first_name": "John",
      "last_name": "Doe",
      "email": "john.doe@neviswealth.com",
      "description": "Business owner interested in retirement planning.",
      "social_links": []
    }
  }
]
```

`GET /search?q=address%20proof` returns a document result like:

```json
[
  {
    "type": "document",
    "score": 0.71,
    "match_type": "concept",
    "matched_text": "Monthly electricity bill for John Doe, 14 Oak Street, London. Account balance GBP 82.40. Billing period September 2026.",
    "document": {
      "id": "55cd7ae8-551e-4330-9e22-cce29998a2f9",
      "client_id": "e61c101c-a542-4b9c-95dc-07e487323187",
      "title": "Utility bill",
      "content": "Monthly electricity bill for John Doe, 14 Oak Street, London. Account balance GBP 82.40. Billing period September 2026.",
      "created_at": "2026-10-29T12:00:00Z",
      "summary": "Monthly electricity bill for John Doe, 14 Oak Street, London. Account balance GBP 82.40. Billing period September 2026.",
      "summary_method": "extractive"
    }
  }
]
```

IDs, timestamps and scores above are illustrative. Live scores have up to six decimal places.
No matches returns `[]` with HTTP 200.

## API contract

The full schema is available at `/openapi.json`, the checked-in [openapi.json](openapi.json),
and `/docs`. The three requested endpoints retain their original paths and response shapes.
Search extends the originally unspecified result objects with typed records and relevance.

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/clients` | Create a client; 201 and a resource `Location` header |
| GET | `/clients/{id}` | Fetch a client; 200 or 404 |
| POST | `/clients/{id}/documents` | Create and index a document atomically; 201 or 404 |
| GET | `/documents/{id}` | Fetch a document including its extractive summary |
| GET | `/search?q=...` | Ranked client and document results |
| GET | `/health` | Database readiness; 200 or 503 |

Search accepts `limit` (1-100, default 20), `offset` (0-10,000, default 0), and optional
`client_id` (UUID). A nonexistent scoped client returns 404. Sorting uses descending score,
then result type and ID for stable pagination on an unchanged dataset. Documents appear
once, even if several chunks match. Results include the best matching chunk.

Validation rejects blank names/titles/content, invalid email or social URLs, unknown
request fields, malformed IDs, and blank/punctuation-only queries. Names are limited to
100 characters, titles to 200, content to 100,000, descriptions to 5,000, queries to 500,
and social links to 10. Request bodies over 512 KiB return 413, including chunked bodies.
Duplicate emails return 409; email uniqueness is case-insensitive. Bad input returns 422.
Database errors return a generic 503 without SQL or credentials.

## Search decisions and limits

**Clients:** Unicode-normalized, case-insensitive substring matching across full name,
email, and description. All query words may also match in a different order. This handles
email fragments such as `NevisWealth` directly. Social links are stored but not searched,
as the brief asks for name, email and description matching.

**Documents:** Content is split into 160-word chunks with a 30-word overlap. Each chunk
is indexed with its title using normalized 256-dimensional
[POTION embeddings](https://huggingface.co/minishlab/potion-base-8M), loaded with
[Model2Vec](https://minish.ai/packages/model2vec/inference/). The immutable model revision
is pinned in `scripts/download_model.py`; neither requests nor startup fetch a model.
Chunking prevents long documents from hiding a relevant paragraph at the end.

General embeddings do not reliably associate every short KYC phrase: in a local probe,
`address proof` had a low similarity to a utility bill. Rather than lower the threshold
and admit irrelevant documents, `concepts.py` records three explicit retrieval associations:
address evidence, identity evidence and income evidence. Only complete recognized queries
are expanded; qualifiers are not silently discarded. A utility bill can therefore match
`address proof`, while unrelated pension or cooking documents do not match by association.
These associations help find evidence; they do not validate it for compliance.

Lexical matches receive strength 1.0 (substring) or 0.85 (all query words); concept matches
receive 0.9. For either, final relevance is `0.75 * strength + 0.25 * cosine_similarity`.
Otherwise a chunk must meet `SEMANTIC_THRESHOLD` (default 0.60) and scores
`0.70 * cosine_similarity`. Cosine is clamped to [0, 1]. Client scores are their lexical
strength. This intentionally favors exact identity matches over approximate documents.
The result's `match_type` is `lexical`, `concept`, `semantic`, or `hybrid` (text/concept
and semantic). Scores are ranking signals, not confidence probabilities.

The threshold is a conservative starting point checked against the included small test
corpus, not a production-calibrated guarantee. Static English embeddings lose word order
and negation; the vocabulary is deliberately finite. Unseen paraphrases, multilingual
documents and ambiguous queries can miss or return imperfect results.

**Summaries:** Representative source sentences are selected by embedding similarity to
the sentence centroid, then restored to source order. Summaries are capped at 600
characters and three selected sentences for longer content; short content is returned
unchanged. At most the first 200 sentences are candidates. This is extractive, not an
LLM-generated analysis, and makes no new factual claims.

**Storage:** Client, document and chunk tables use real foreign keys. Document text,
summary and embeddings commit in one transaction. Vectors are persisted as JSON and
scanned in batches; there is no process-local search index to become stale after restart.
This keeps the assignment easy to inspect and portable across SQLite/Postgres. Search is
linear in clients and chunks and currently ranks all candidates before paginating. At
larger scale I would add PostgreSQL trigram/full-text indexes and pgvector with filtered
nearest-neighbor retrieval, then measure recall and latency before tuning the index.
Changing the model requires an explicit reindex of every existing chunk.

## Tests and checks

```sh
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=nevis_search --cov-report=term-missing
```

API tests use a deterministic encoder so business logic does not depend on model
downloads. Tests marked `semantic` use the real model when `assets/model` is present;
download it first to include those checks. PostgreSQL tests use `TEST_DATABASE_URL` and
an isolated test schema; they are skipped when the variable is absent. CI runs both
real-model checks and PostgreSQL integration checks, as well as formatting and linting.

## Deployment and access

The repository includes `vercel.json` and a root `main.py` for a Python deployment on
Vercel. A serverless deployment **must** use external PostgreSQL; SQLite in `/tmp` is not
durable and is deliberately rejected when `VERCEL=1`.

Set these runtime variables:

```text
DATABASE_URL=postgresql+psycopg://...  # dedicated DB with TLS
API_KEY=<a randomly generated secret of at least 32 characters>
REQUIRE_API_KEY=true
```

Fetch the pinned model in the build step (`uv run python scripts/download_model.py`) and
apply migrations once against the target database before serving traffic. The local
`assets/model` directory must be included in the function bundle. Set the function region
near the database. The Docker image can alternatively run on any container host with
PostgreSQL; supply the same environment variables and an HTTPS endpoint.

Send the key as `X-API-Key` on all data endpoints, or use **Authorize** in Swagger UI.
For scripts, export `API_KEY` before running `seed_demo.py`. `/health`, `/docs` and
`/openapi.json` expose no stored client/document data and remain public.

The single shared key is appropriate for a reviewer sandbox, not advisor-level access
control. This is not a multi-tenant production system. Use synthetic data only. A real
deployment needs per-advisor authorization, tenant-scoped queries, managed secrets,
rate limits, backups and retention policy. Application access logs are disabled in the
documented run commands because query strings can contain client information; configure
the hosting provider's logs separately before using real personal data.

## Layout

```text
src/nevis_search/
  app.py          HTTP routes, dependencies and error mapping
  config.py       validated environment configuration
  database.py     engine setup
  models.py       persistence models
  schemas.py      request and response contracts
  service.py      writes and ranked retrieval
  embeddings.py  local encoder and its test seam
  concepts.py     explicit KYC retrieval vocabulary
  text.py         normalization, chunking and extractive summaries
migrations/       versioned schema
scripts/          model download and synthetic demo seed
tests/            API, search, storage and real-model checks
```

The API is intentionally synchronous: database and CPU inference work run in FastAPI's
thread pool. There is no message queue or background indexing state to coordinate for
this dataset. The embedding model's model card identifies its license as MIT; its
downloaded README is retained with the model artifacts.
