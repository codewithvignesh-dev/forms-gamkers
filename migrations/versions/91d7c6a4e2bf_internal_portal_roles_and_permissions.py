"""Use fixed portal roles and configurable permissions.

Revision ID: 91d7c6a4e2bf
Revises: c84a1f2d9e30
Create Date: 2026-11-06 15:06:00
"""
from alembic import op
import sqlalchemy as sa


revision = "91d7c6a4e2bf"
down_revision = "c84a1f2d9e30"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("admin_slot", sa.Integer(), nullable=True))
        batch_op.create_unique_constraint("uq_user_admin_slot", ["admin_slot"])

    with op.batch_alter_table("invitation") as batch_op:
        batch_op.add_column(
            sa.Column(
                "role",
                sa.String(length=24),
                nullable=False,
                server_default="contributor",
            )
        )

    bind = op.get_bind()
    admin_id = bind.execute(
        sa.text("SELECT id FROM user WHERE `role` = 'org_admin' ORDER BY id LIMIT 1")
    ).scalar()
    bind.execute(sa.text("UPDATE user SET `role` = 'contributor' WHERE `role` = 'member'"))
    bind.execute(sa.text("UPDATE user SET `role` = 'developer' WHERE `role` = 'org_admin'"))
    if admin_id is not None:
        bind.execute(
            sa.text("UPDATE user SET `role` = 'admin', admin_slot = 1 WHERE id = :user_id"),
            {"user_id": admin_id},
        )
        bind.execute(
            sa.text(
                "UPDATE organization SET name = 'GAMKERS PORTAL' "
                "WHERE id = (SELECT organization_id FROM user WHERE id = :user_id)"
            ),
            {"user_id": admin_id},
        )

    op.create_table(
        "role_permission",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("permission", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_role_permission_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_role_permission"),
        sa.UniqueConstraint(
            "organization_id", "role", "permission", name="uq_role_permission"
        ),
    )


def downgrade():
    op.drop_table("role_permission")
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE user SET `role` = 'member' WHERE `role` IN ('developer', 'contributor')"))
    bind.execute(sa.text("UPDATE user SET `role` = 'org_admin', admin_slot = NULL WHERE `role` = 'admin'"))
    with op.batch_alter_table("invitation") as batch_op:
        batch_op.drop_column("role")
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_constraint("uq_user_admin_slot", type_="unique")
        batch_op.drop_column("admin_slot")
