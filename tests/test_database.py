from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from conftest import FakeEncoder, make_api
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from nevis_search.database import build_engine
from nevis_search.schemas import ClientCreate, DocumentCreate
from nevis_search.service import create_client, create_document, search


def test_sqlite_data_survives_app_restart(tmp_path: Path):
    database_path = tmp_path / "persistent.sqlite3"
    encoder = FakeEncoder()
    with make_api(database_path, encoder) as api:
        owner = api.post(
            "/clients", json={"first_name": "Sam", "last_name": "Stone", "email": "sam@example.com"}
        ).json()
        document = api.post(
            f"/clients/{owner['id']}/documents",
            json={"title": "Restart record", "content": "restarttoken persists across launch."},
        ).json()

    with make_api(database_path, encoder) as restarted:
        assert restarted.get(f"/clients/{owner['id']}").status_code == 200
        assert restarted.get(f"/documents/{document['id']}").status_code == 200
        results = restarted.get("/search", params={"q": "restarttoken"}).json()
        assert [item["document"]["id"] for item in results if item["type"] == "document"] == [
            document["id"]
        ]


def test_sqlite_migration_can_upgrade_and_downgrade_an_isolated_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    database_path = tmp_path / "migration.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.setenv("VERCEL", "false")
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))

    command.upgrade(config, "head")
    engine = build_engine(f"sqlite:///{database_path}")
    assert {"clients", "documents", "document_chunks"} <= set(inspect(engine).get_table_names())
    engine.dispose()

    command.downgrade(config, "base")
    engine = build_engine(f"sqlite:///{database_path}")
    assert not {"clients", "documents", "document_chunks"} & set(inspect(engine).get_table_names())
    engine.dispose()


@pytest.mark.postgres
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set")
def test_postgres_roundtrip_uses_a_temporary_schema_only(monkeypatch):
    database_url = os.environ["TEST_DATABASE_URL"]
    schema = f"nevis_test_{uuid4().hex}"
    admin_engine = build_engine(database_url)
    isolated_engine = None
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        isolated_url = make_url(database_url).update_query_dict(
            {"options": f"-csearch_path={schema}"}
        )
        isolated_engine = build_engine(isolated_url.render_as_string(hide_password=False))
        monkeypatch.setenv("DATABASE_URL", isolated_url.render_as_string(hide_password=False))
        project_root = Path(__file__).resolve().parents[1]
        command.upgrade(Config(str(project_root / "alembic.ini")), "head")
        with isolated_engine.connect() as connection:
            secured_tables = connection.scalars(
                text(
                    "SELECT relname FROM pg_class JOIN pg_namespace n ON n.oid = relnamespace "
                    "WHERE n.nspname = :schema AND relrowsecurity"
                ),
                {"schema": schema},
            ).all()
        assert set(secured_tables) == {"clients", "documents", "document_chunks"}

        encoder = FakeEncoder()
        with Session(isolated_engine, expire_on_commit=False) as session:
            owner = create_client(
                session,
                ClientCreate(
                    first_name="Postgres",
                    last_name="Probe",
                    email=f"probe-{uuid4().hex}@example.com",
                ),
            )
            document = create_document(
                session,
                owner.id,
                DocumentCreate(title="Database probe", content="postgresprobe"),
                encoder,
            )
            results = search(session, "postgresprobe", encoder, 0.60, 10, 0)
            docs = [result for result in results if getattr(result, "type", None) == "document"]
            assert len(docs) == 1
            assert docs[0].document.id == document.id
    finally:
        try:
            if isolated_engine is not None:
                isolated_engine.dispose()
        finally:
            try:
                with admin_engine.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            finally:
                admin_engine.dispose()
