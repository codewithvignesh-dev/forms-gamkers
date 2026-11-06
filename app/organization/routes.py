from datetime import timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db, limiter
from app.mail import send_email
from app.auth.models import Invitation, RolePermission, User
from app.organization.models import Organization
from app.permissions import (
    DEFAULT_ROLE_PERMISSIONS,
    PROTECTED_PERMISSIONS,
    ROLE_LABELS,
    ROLE_PERMISSIONS,
    requires_permission,
)
from app.security import encrypt_smtp_password, hash_token, new_token, valid_email
from app.urls import absolute_app_url
from app.utils import utcnow

organization = Blueprint("organization", __name__)


def require_admin():
    if not current_user.is_org_admin:
        flash("Only organisation admins can change these settings.", "error")
        return redirect(url_for("main.dashboard"))
    return None


@organization.route("/settings/organisation", methods=["GET", "POST"])
@login_required
@requires_permission("settings.manage")
def settings():
    org = db.session.get(Organization, current_user.organization_id)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        website = request.form.get("website", "").strip()
        if not name or len(name) > 160 or len(website) > 255:
            flash("Enter a valid organisation name and website.", "error")
        else:
            org.name = name
            org.website = website or None
            db.session.commit()
            flash("Organisation details saved.", "success")
            return redirect(url_for("organization.settings"))
    return render_template("organization/settings.html", organization=org)


@organization.route("/settings/email", methods=["GET", "POST"])
@login_required
@requires_permission("settings.manage")
def email_settings():
    org = db.session.get(Organization, current_user.organization_id)
    if request.method == "POST":
        host = request.form.get("host", "").strip()
        sender = request.form.get("sender", "").strip().lower()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        security = request.form.get("security", "starttls")
        try:
            port = int(request.form.get("port", ""))
        except ValueError:
            port = 0
        if not host or len(host) > 255 or not 1 <= port <= 65535:
            flash("Enter a valid SMTP host and port.", "error")
        elif not valid_email(sender) or security not in {"starttls", "ssl"}:
            flash("Enter a valid sender address and connection security.", "error")
        else:
            try:
                encrypted_password = encrypt_smtp_password(password) if password else None
            except RuntimeError as error:
                flash(str(error), "error")
                return render_template("organization/email_settings.html", organization=org)
            org.smtp_host = host
            org.smtp_port = port
            org.smtp_username = username or None
            org.smtp_sender = sender
            org.smtp_security = security
            if password:
                org.smtp_password_encrypted = encrypted_password
            db.session.commit()
            flash("SMTP settings saved. The password is never shown again.", "success")
            return redirect(url_for("organization.email_settings"))
    return render_template("organization/email_settings.html", organization=org)


@organization.route("/team", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per hour", methods=["POST"])
def team():
    denied = require_admin()
    if denied:
        return denied
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", "contributor")
        if role not in {"developer", "contributor"}:
            flash("Choose the developer or contributor role.", "error")
        elif not valid_email(email):
            flash("Enter a valid email address.", "error")
        elif db.session.scalar(db.select(User.id).where(func.lower(User.email) == email)):
            flash("That email already has an account.", "error")
        elif db.session.scalar(
            db.select(Invitation.id).where(
                Invitation.organization_id == current_user.organization_id,
                Invitation.email == email,
                Invitation.accepted_at.is_(None),
                Invitation.expires_at > utcnow(),
            )
        ):
            flash("An active invitation already exists for that email.", "error")
        else:
            token = new_token()
            invite = Invitation(
                organization_id=current_user.organization_id,
                email=email,
                role=role,
                token_hash=hash_token(token),
                expires_at=utcnow() + timedelta(days=7),
            )
            db.session.add(invite)
            db.session.commit()
            try:
                link = absolute_app_url("auth.accept_invitation", token=token)
                sent = send_email(
                    email,
                    f"Join {current_user.organization.name} on GAMKERS PORTAL",
                    f"You have been invited to join {current_user.organization.name} as a {ROLE_LABELS[role]}.\n\nAccept within 7 days:\n{link}",
                    current_user.organization,
                )
            except Exception:
                current_app.logger.exception("Organisation invitation email delivery failed.")
                sent = False
            if sent:
                flash("Invitation sent.", "success")
            else:
                db.session.delete(invite)
                db.session.commit()
                flash("Invitation email could not be sent. Configure SMTP and try again.", "error")
            return redirect(url_for("organization.team"))
    members = User.query.filter_by(organization_id=current_user.organization_id).order_by(User.created_at).all()
    invitations = Invitation.query.filter_by(
        organization_id=current_user.organization_id, accepted_at=None
    ).filter(Invitation.expires_at > utcnow()).all()
    return render_template(
        "organization/team.html",
        members=members,
        invitations=invitations,
        roles=ROLE_LABELS,
        permission_rows=ROLE_PERMISSIONS,
        permission_defaults=DEFAULT_ROLE_PERMISSIONS,
        permission_overrides={
            (item.role, item.permission): item.enabled
            for item in RolePermission.query.filter_by(
                organization_id=current_user.organization_id
            ).all()
        },
        protected_permissions=PROTECTED_PERMISSIONS,
    )


@organization.post("/team/<int:user_id>/role")
@login_required
def update_role(user_id):
    denied = require_admin()
    if denied:
        return denied
    user = User.query.filter_by(
        id=user_id, organization_id=current_user.organization_id
    ).first_or_404()
    role = request.form.get("role")
    if user.id == current_user.id:
        flash("You cannot change your own role.", "error")
    elif role not in {"developer", "contributor"}:
        flash("Choose the developer or contributor role. The single admin cannot be reassigned.", "error")
    else:
        user.role = role
        user.admin_slot = None
        db.session.commit()
        flash("Member role updated.", "success")
    return redirect(url_for("organization.team"))


@organization.post("/team/permissions")
@login_required
def update_permissions():
    denied = require_admin()
    if denied:
        return denied
    role_permissions = {
        (item.role, item.permission): item
        for item in RolePermission.query.filter_by(
            organization_id=current_user.organization_id
        ).all()
    }
    for role in ("admin", "developer", "contributor"):
        for permission, _label in ROLE_PERMISSIONS:
            enabled = request.form.get(f"{role}:{permission}") == "on"
            if permission in PROTECTED_PERMISSIONS:
                enabled = role == "admin"
            existing = role_permissions.get((role, permission))
            if existing is None:
                existing = RolePermission(
                    organization_id=current_user.organization_id,
                    role=role,
                    permission=permission,
                    enabled=enabled,
                )
                db.session.add(existing)
            else:
                existing.enabled = enabled
    db.session.commit()
    flash("Role permissions updated.", "success")
    return redirect(url_for("organization.team"))
