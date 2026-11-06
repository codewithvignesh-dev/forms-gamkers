from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.permissions import (
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSION_NAMES,
    PROTECTED_PERMISSIONS,
)
from app.utils import utcnow


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(24), nullable=False, default="contributor")
    admin_slot = db.Column(db.Integer, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    organization = db.relationship("Organization", back_populates="users")
    forms = db.relationship("Form", back_populates="creator")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_org_admin(self):
        return self.role == "admin" and self.admin_slot == 1

    def has_permission(self, permission):
        if self.role not in DEFAULT_ROLE_PERMISSIONS:
            return False
        if permission not in PERMISSION_NAMES:
            return False
        if self.role == "admin" and not self.is_org_admin:
            return False
        if self.is_org_admin and permission in PROTECTED_PERMISSIONS:
            return True
        if permission in PROTECTED_PERMISSIONS:
            return False

        override = db.session.scalar(
            db.select(RolePermission.enabled).where(
                RolePermission.organization_id == self.organization_id,
                RolePermission.role == self.role,
                RolePermission.permission == permission,
            )
        )
        return (
            override
            if override is not None
            else permission in DEFAULT_ROLE_PERMISSIONS[self.role]
        )


class RolePermission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    role = db.Column(db.String(24), nullable=False)
    permission = db.Column(db.String(64), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False)
    __table_args__ = (
        db.UniqueConstraint(
            "organization_id", "role", "permission", name="uq_role_permission"
        ),
    )


class PasswordReset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)


class Invitation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(24), nullable=False, default="contributor")
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    accepted_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    organization = db.relationship("Organization")
