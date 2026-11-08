"""Turn on row level security for every table (PostgreSQL only).

Hosted databases such as Supabase publish the `public` schema through a REST API that anyone holding the project's
public key can call. With row level security on and no policies, that API sees nothing. This app is not affected:
it connects as the table owner (the `postgres` role), which is exempt from row level security.

Tables added later need the same line (ALTER TABLE ... ENABLE ROW LEVEL SECURITY) in their migration.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _tables() -> list[str]:
    insp = sa.inspect(op.get_bind())
    return [t for t in insp.get_table_names(schema="public") if t != "alembic_version"]


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _tables():
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _tables():
        op.execute(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY')
