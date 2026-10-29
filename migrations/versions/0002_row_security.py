"""Deny direct public-role access on PostgreSQL managed platforms.

The API connects as the table owner. No public-role policies are granted: client
data must be accessed through the API, including when a host exposes a Data API.
SQLite does not expose a separate SQL API and needs no equivalent operation.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = ("clients", "documents", "document_chunks")


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
