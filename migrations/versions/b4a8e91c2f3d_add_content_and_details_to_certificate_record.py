"""Add content and details to certificate_record.

Revision ID: b4a8e91c2f3d
Revises: f3a710d9c642
"""
from alembic import op
import sqlalchemy as sa


revision = "b4a8e91c2f3d"
down_revision = "f3a710d9c642"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("certificate_record") as batch_op:
        batch_op.add_column(sa.Column("title", sa.String(length=180), nullable=True))
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("rendered_content", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("issue_date", sa.String(length=64), nullable=True))


def downgrade():
    with op.batch_alter_table("certificate_record") as batch_op:
        batch_op.drop_column("issue_date")
        batch_op.drop_column("rendered_content")
        batch_op.drop_column("description")
        batch_op.drop_column("title")
