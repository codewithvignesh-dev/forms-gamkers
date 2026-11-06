from datetime import timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func

from app.extensions import db, limiter
from app.mail import send_email
from app.auth.models import Invitation, PasswordReset, User
from app.security import hash_token, new_token, valid_email
from app.urls import absolute_app_url
from app.utils import utcnow

auth = Blueprint("auth", __name__)


@auth.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = db.session.scalar(db.select(User).where(User.email == email))
        password = request.form.get("password", "")
        if user and len(password) <= 1024 and user.check_password(password):
            login_user(user, remember=request.form.get("remember") == "on")
            next_url = request.args.get("next", "")
            if next_url.startswith("/") and not next_url.startswith("//") and "\\" not in next_url:
                return redirect(next_url)
            return redirect(url_for("main.dashboard"))
        flash("Email or password is incorrect.", "error")
    return render_template("auth/login.html")


@auth.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.home"))


@auth.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = db.session.scalar(db.select(User).where(User.email == email)) if valid_email(email) else None
        if user:
            PasswordReset.query.filter_by(user_id=user.id, used_at=None).update(
                {"used_at": utcnow()}, synchronize_session=False
            )
            token = new_token()
            reset = PasswordReset(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=utcnow() + timedelta(minutes=30),
            )
            db.session.add(reset)
            db.session.commit()
            try:
                link = absolute_app_url("auth.reset_password", token=token)
                if not send_email(user.email, "Reset your GAMKERS PORTAL password", f"Reset your password within 30 minutes:\n\n{link}", user.organization):
                    current_app.logger.warning("Password reset email not sent: no SMTP configuration.")
            except Exception:
                current_app.logger.exception("Password reset email delivery failed.")
        flash("If an account matches that email, a password reset link will be sent.", "info")
        return redirect(url_for("auth.forgot_password"))
    return render_template("auth/forgot_password.html")


@auth.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def reset_password(token):
    reset = db.session.scalar(
        db.select(PasswordReset).where(PasswordReset.token_hash == hash_token(token))
    )
    if not reset or reset.used_at or reset.expires_at <= utcnow():
        flash("This password reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if not 12 <= len(password) <= 1024:
            flash("Use a password with 12–1,024 characters.", "error")
        else:
            user = db.session.get(User, reset.user_id)
            user.set_password(password)
            reset.used_at = utcnow()
            db.session.commit()
            flash("Your password has been updated. Please sign in.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html")


@auth.route("/invite/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def accept_invitation(token):
    invitation = db.session.scalar(
        db.select(Invitation).where(Invitation.token_hash == hash_token(token))
    )
    if not invitation or invitation.accepted_at or invitation.expires_at <= utcnow():
        flash("This invitation is invalid or has expired.", "error")
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        if not name or len(name) > 120 or not 12 <= len(password) <= 1024:
            flash("Enter your name and a password with 12–1,024 characters.", "error")
        elif db.session.scalar(db.select(User.id).where(func.lower(User.email) == invitation.email)):
            flash("An account already exists for this email address.", "error")
        else:
            user = User(
                organization_id=invitation.organization_id,
                email=invitation.email,
                name=name,
                role=invitation.role,
            )
            user.set_password(password)
            invitation.accepted_at = utcnow()
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for("main.dashboard"))
    return render_template("auth/accept_invitation.html", invitation=invitation)
