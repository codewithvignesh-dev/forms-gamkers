"""Seed the configured initial administrator.

Revision ID: e8c21f74b603
Revises: b63c41d9a725
"""
from datetime import datetime, timezone
import os

from alembic import op
import sqlalchemy as sa
from email_validator import EmailNotValidError, validate_email
from werkzeug.security import generate_password_hash


revision = "e8c21f74b603"
down_revision = "b63c41d9a725"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    existing_admin = bind.execute(
        sa.text(
            "SELECT id, admin_slot FROM user "
            "WHERE role = 'admin' ORDER BY id LIMIT 1"
        )
    ).first()
    if existing_admin:
        if existing_admin.admin_slot != 1:
            raise RuntimeError(
                "An admin account exists without the protected admin slot; "
                "resolve it before upgrading."
            )
        return

    name = (os.environ.get("INITIAL_ADMIN_NAME") or "").strip()
    email = (os.environ.get("INITIAL_ADMIN_EMAIL") or "").strip().lower()
    password = os.environ.get("INITIAL_ADMIN_PASSWORD") or ""
    if not name or not email or not password:
        raise RuntimeError(
            "Set INITIAL_ADMIN_NAME, INITIAL_ADMIN_EMAIL, and "
            "INITIAL_ADMIN_PASSWORD in the environment before the initial migration."
        )
    if len(name) > 120:
        raise RuntimeError("INITIAL_ADMIN_NAME must be at most 120 characters.")
    if len(password) < 12 or len(password) > 1024:
        raise RuntimeError(
            "INITIAL_ADMIN_PASSWORD must contain 12 to 1,024 characters."
        )
    try:
        email = validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError as error:
        raise RuntimeError(
            "INITIAL_ADMIN_EMAIL must be a valid email address."
        ) from error
    if bind.execute(
        sa.text("SELECT id FROM user WHERE lower(email) = :email LIMIT 1"),
        {"email": email},
    ).first():
        raise RuntimeError(
            "INITIAL_ADMIN_EMAIL already belongs to a non-admin account."
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    organization_id = bind.execute(
        sa.text("SELECT id FROM organization ORDER BY id LIMIT 1")
    ).scalar()
    if organization_id is None:
        organization = sa.Table("organization", sa.MetaData(), autoload_with=bind)
        organization_id = bind.execute(
            organization.insert().values(
                name="GAMKERS PORTAL",
                created_at=now,
                smtp_security="starttls",
            )
        ).inserted_primary_key[0]
    else:
        bind.execute(
            sa.text("UPDATE organization SET name = :name WHERE id = :id"),
            {"name": "GAMKERS PORTAL", "id": organization_id},
        )

    user = sa.table(
        "user",
        sa.column("organization_id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("email", sa.String),
        sa.column("password_hash", sa.String),
        sa.column("role", sa.String),
        sa.column("admin_slot", sa.Integer),
        sa.column("created_at", sa.DateTime),
    )
    bind.execute(
        user.insert().values(
            organization_id=organization_id,
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            role="admin",
            admin_slot=1,
            created_at=now,
        )
    )


def downgrade():
    # Keep the seeded administrator as durable application data.
    pass
