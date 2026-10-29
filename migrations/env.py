from alembic import context

from nevis_search import models  # noqa: F401 - registers the schema
from nevis_search.config import Settings
from nevis_search.database import Base, build_engine


def run_migrations() -> None:
    engine = build_engine(Settings().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
