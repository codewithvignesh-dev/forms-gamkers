"""Add certificate PDF expiry and form banner image storage.

Revision ID: b63c41d9a725
Revises: 91d7c6a4e2bf
Create Date: 2026-11-06 17:00:00
"""
from datetime import timedelta

from alembic import op
import sqlalchemy as sa


revision = "b63c41d9a725"
down_revision = "91d7c6a4e2bf"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("form", sa.Column("banner_image_path", sa.String(length=80)))
    op.add_column("certificate_record", sa.Column("pdf_expires_at", sa.DateTime()))
    op.add_column("certificate_record", sa.Column("pdf_deleted_at", sa.DateTime()))
    op.create_index(
        "ix_certificate_record_pdf_expires_at",
        "certificate_record",
        ["pdf_expires_at"],
    )

    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        bind.execute(
            sa.text(
                "UPDATE certificate_record "
                "SET pdf_expires_at = DATE_ADD(created_at, INTERVAL 30 DAY) "
                "WHERE status = 'generated' AND pdf_path IS NOT NULL"
            )
        )
    elif bind.dialect.name == "sqlite":
        bind.execute(
            sa.text(
                "UPDATE certificate_record "
                "SET pdf_expires_at = datetime(created_at, '+30 days') "
                "WHERE status = 'generated' AND pdf_path IS NOT NULL"
            )
        )
    else:
        records = bind.execute(
            sa.text(
                "SELECT id, created_at FROM certificate_record "
                "WHERE status = 'generated' AND pdf_path IS NOT NULL"
            )
        ).all()
        for record_id, created_at in records:
            bind.execute(
                sa.text(
                    "UPDATE certificate_record SET pdf_expires_at = :expires_at WHERE id = :id"
                ),
                {"id": record_id, "expires_at": created_at + timedelta(days=30)},
            )


def downgrade():
    op.drop_index(
        "ix_certificate_record_pdf_expires_at", table_name="certificate_record"
    )
    op.drop_column("certificate_record", "pdf_deleted_at")
    op.drop_column("certificate_record", "pdf_expires_at")
    op.drop_column("form", "banner_image_path")
