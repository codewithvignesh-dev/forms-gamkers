from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.auth.models import User
from app.extensions import db
from app.forms.models import Form, Submission

main = Blueprint("main", __name__)


@main.get("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return render_template("landing.html")


@main.get("/dashboard")
@login_required
def dashboard():
    forms = (
        Form.query.filter_by(organization_id=current_user.organization_id)
        .order_by(Form.updated_at.desc())
        .all()
    )
    response_counts = {}
    if forms:
        response_counts = dict(
            db.session.query(Submission.form_id, func.count(Submission.id))
            .filter(Submission.form_id.in_([form.id for form in forms]))
            .group_by(Submission.form_id)
            .all()
        )
    total_responses = sum(response_counts.values())
    member_count = User.query.filter_by(organization_id=current_user.organization_id).count()
    return render_template(
        "dashboard.html",
        forms=forms,
        member_count=member_count,
        response_counts=response_counts,
        total_responses=total_responses,
        can_manage_forms=current_user.has_permission("forms.manage"),
        can_view_responses=current_user.has_permission("responses.view"),
        can_export_responses=current_user.has_permission("responses.export"),
    )
