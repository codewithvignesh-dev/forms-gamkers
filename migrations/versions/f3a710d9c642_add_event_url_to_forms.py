"""Add an optional event URL to forms.

Revision ID: f3a710d9c642
Revises: e8c21f74b603
"""
from alembic import op
import sqlalchemy as sa


revision = "f3a710d9c642"
down_revision = "e8c21f74b603"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("form", sa.Column("event_url", sa.String(length=2048)))


def downgrade():
    op.drop_column("form", "event_url")
