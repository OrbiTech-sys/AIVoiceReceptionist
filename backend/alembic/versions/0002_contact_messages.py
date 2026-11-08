"""contact form messages

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
import app.db


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('contact_messages',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('business', sa.String(length=160), nullable=False),
    sa.Column('topic', sa.String(length=16), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('handled_at', app.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_at', app.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.db.UTCDateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_contact_messages_created', 'contact_messages', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_contact_messages_created', table_name='contact_messages')
    op.drop_table('contact_messages')
