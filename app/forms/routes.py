import json
import uuid
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    stream_with_context,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func

from app.extensions import db, limiter
from app.forms.csv_export import export_filename, iter_csv_rows
from app.forms.banner_storage import remove_banner, save_banner
from app.forms.models import Form, Submission
from app.forms.validation import QUESTION_TYPES, validate_answers, validate_questions
from app.mail import send_email
from app.permissions import requires_permission
from app.urls import absolute_app_url
from app.utils import utcnow


forms = Blueprint("forms", __name__)


def owned_form(form_id):
    return Form.query.filter_by(
        id=form_id, organization_id=current_user.organization_id
    ).first_or_404()


def render_editor(form, status=400):
    initial_questions = form.questions if form else []
    raw_questions = request.form.get("questions")
    if raw_questions:
        try:
            parsed = json.loads(raw_questions)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            initial_questions = []
            for item in parsed[:30]:
                if not isinstance(item, dict):
                    continue
                question_type = item.get("type")
                options = item.get("options", [])
                if not isinstance(options, list):
                    options = []
                initial_questions.append(
                    {
                        "id": item.get("id") if isinstance(item.get("id"), str) else "",
                        "title": item.get("title") if isinstance(item.get("title"), str) else "",
                        "type": (
                            question_type
                            if isinstance(question_type, str) and question_type in QUESTION_TYPES
                            else "short_text"
                        ),
                        "required": item.get("required") is True,
                        "options": [
                            value[:120] for value in options if isinstance(value, str)
                        ][:20],
                    }
                )
    return render_template(
        "forms/editor.html", form=form, initial_questions=initial_questions
    ), status


def parse_event_time(value, field_name):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"Enter a valid event {field_name}.") from error
    if parsed.tzinfo is not None:
        raise ValueError(f"Enter the event {field_name} in the selected timezone.")
    return parsed


@forms.route("/forms/new", methods=["GET", "POST"])
@login_required
@requires_permission("forms.manage")
def create_form():
    if request.method == "POST":
        return save_form(None)
    return render_template("forms/editor.html", form=None)


@forms.route("/forms/<int:form_id>/edit", methods=["GET", "POST"])
@login_required
@requires_permission("forms.manage")
def edit_form(form_id):
    form = owned_form(form_id)
    if form.closed_at:
        feedback_form = (
            db.session.scalar(db.select(Form).where(Form.parent_form_id == form.id))
            if not form.parent_form_id
            else None
        )
        parent_form = db.session.get(Form, form.parent_form_id) if form.parent_form_id else None
        return render_template(
            "forms/closed_admin.html",
            form=form,
            feedback_form=feedback_form,
            parent_form=parent_form,
            feedback_url=(
                absolute_app_url(
                    "forms.public_form", public_id=feedback_form.public_id
                )
                if feedback_form
                else None
            ),
        )
    if request.method == "POST":
        return save_form(form)
    return render_template("forms/editor.html", form=form)


def save_form(form):
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    subject = request.form.get("confirmation_subject", "").strip()
    body = request.form.get("confirmation_body", "").strip()
    confirmation_format = request.form.get("confirmation_format", "text")
    event_timezone = request.form.get("event_timezone", "Asia/Kolkata").strip()
    event_location = request.form.get("event_location", "").strip()
    event_url = request.form.get("event_url", "").strip()
    if not title or len(title) > 180 or len(description) > 5000:
        flash("Enter a title and a description under 5,000 characters.", "error")
        return render_editor(form)
    if not subject or len(subject) > 200 or "\r" in subject or "\n" in subject:
        flash("Enter an email subject of up to 200 characters on one line.", "error")
        return render_editor(form)
    if not body or len(body) > 5000:
        flash("Enter a confirmation email message under 5,000 characters.", "error")
        return render_editor(form)
    if confirmation_format not in {"text", "html", "markdown"}:
        flash("Choose a supported confirmation email format.", "error")
        return render_editor(form)
    if len(event_timezone) > 64 or len(event_location) > 255 or len(event_url) > 2048:
        flash(
            "Keep the event timezone under 64, location under 255, and URL under 2,048 characters.",
            "error",
        )
        return render_editor(form)
    if event_url:
        try:
            parsed_event_url = urlsplit(event_url)
            valid_event_url = (
                parsed_event_url.scheme.lower() == "https"
                and bool(parsed_event_url.hostname)
                and not any(character.isspace() for character in event_url)
                and "\\" not in event_url
                and not parsed_event_url.username
                and not parsed_event_url.password
                and parsed_event_url.port != 0
                and not any(ord(character) < 32 for character in event_url)
            )
        except ValueError:
            valid_event_url = False
        if not valid_event_url:
            flash("Enter a valid HTTPS event URL.", "error")
            return render_editor(form)
    try:
        ZoneInfo(event_timezone)
        event_start_at = parse_event_time(
            request.form.get("event_start_at", "").strip(), "start time"
        )
        event_end_at = parse_event_time(
            request.form.get("event_end_at", "").strip(), "end time"
        )
    except (ValueError, ZoneInfoNotFoundError) as error:
        flash(str(error).strip() or "Enter a valid IANA event timezone.", "error")
        return render_editor(form)
    if event_end_at and not event_start_at:
        flash("Set an event start time before adding an end time.", "error")
        return render_editor(form)
    if event_start_at and event_end_at and event_end_at <= event_start_at:
        flash("The event end time must be after its start time.", "error")
        return render_editor(form)
    if event_start_at:
        event_zone = ZoneInfo(event_timezone)
        for value, label in ((event_start_at, "start"), (event_end_at, "end")):
            if value and value.replace(tzinfo=event_zone).astimezone(
                ZoneInfo("UTC")
            ).astimezone(event_zone).replace(tzinfo=None) != value:
                flash(f"The event {label} time does not exist in this timezone.", "error")
                return render_editor(form)
    try:
        questions = validate_questions(request.form.get("questions", ""))
    except ValueError as error:
        flash(str(error), "error")
        return render_editor(form)
    should_publish = (
        form.is_published or request.form.get("publish") == "yes"
        if form
        else request.form.get("publish") == "yes"
    )
    if should_publish and not any(
        question["type"] == "email" and question["required"] for question in questions
    ):
        flash("Add a required email question before publishing so respondents can receive confirmations.", "error")
        return render_editor(form)
    banner_upload = request.files.get("banner_image")
    new_banner_path = None
    if banner_upload and banner_upload.filename:
        try:
            new_banner_path = save_banner(banner_upload)
        except ValueError as error:
            flash(str(error), "error")
            return render_editor(form)
    previous_banner_path = form.banner_image_path if form else None
    if form is None:
        form = Form(
            organization_id=current_user.organization_id,
            creator_id=current_user.id,
            public_id=uuid.uuid4().hex,
        )
    form.title = title
    form.description = description
    if new_banner_path:
        form.banner_image_path = new_banner_path
    form.questions = questions
    form.confirmation_subject = subject
    form.confirmation_body = body
    form.confirmation_format = confirmation_format
    form.event_start_at = event_start_at
    form.event_end_at = event_end_at
    form.event_timezone = event_timezone
    form.event_location = event_location or None
    form.event_url = event_url or None
    form.is_published = should_publish
    db.session.add(form)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        remove_banner(new_banner_path)
        raise
    if new_banner_path and previous_banner_path:
        remove_banner(previous_banner_path)
    flash("Form published." if form.is_published else "Draft saved.", "success")
    return redirect(url_for("forms.edit_form", form_id=form.id))


@forms.post("/forms/<int:form_id>/close")
@login_required
@requires_permission("forms.manage")
def close_form(form_id):
    form = owned_form(form_id)
    if not form.is_published or form.closed_at:
        flash("Only open, published forms can be closed.", "error")
        return redirect(url_for("forms.edit_form", form_id=form.id))
    message = request.form.get("closed_message", "").strip()
    if not message or len(message) > 2000:
        flash("Enter a form-closed message of up to 2,000 characters.", "error")
        return redirect(url_for("forms.edit_form", form_id=form.id))
    feedback_questions = [
        {
            "id": "certificate_name",
            "title": "Full name for your certificate",
            "type": "short_text",
            "required": True,
            "options": [],
        },
        {
            "id": "college_name",
            "title": "Institution or organisation",
            "type": "short_text",
            "required": True,
            "options": [],
        },
        {
            "id": "email",
            "title": "Email address",
            "type": "email",
            "required": True,
            "options": [],
        },
        {
            "id": "rating",
            "title": "How would you rate this event?",
            "type": "choice",
            "required": True,
            "options": ["Excellent", "Good", "Average", "Needs improvement"],
        },
        {
            "id": "feedback",
            "title": "What did you think of the event?",
            "type": "long_text",
            "required": False,
            "options": [],
        },
    ]
    if not form.parent_form_id:
        feedback_form = Form(
            organization_id=form.organization_id,
            creator_id=current_user.id,
            parent_form_id=form.id,
            title=f"{form.title[:166]} — Feedback",
            description="Share your feedback and tell us how you would like your certificate details to appear.",
            public_id=uuid.uuid4().hex,
            questions=feedback_questions,
            confirmation_subject=f"Thanks for your feedback — {form.title[:130]}",
            confirmation_body=(
                "Hello {certificate_name},\n\n"
                "Thank you for sharing your feedback on {form_title}. "
                "Your response has been received."
            ),
            confirmation_format="text",
            is_published=True,
        )
        db.session.add(feedback_form)
    form.closed_at = utcnow()
    form.closed_message = message
    db.session.commit()
    flash(
        "Feedback form is ready to share." if not form.parent_form_id else "Feedback form closed.",
        "success",
    )
    return redirect(url_for("forms.edit_form", form_id=form.id))


@forms.post("/forms/<int:form_id>/delete")
@login_required
@requires_permission("forms.manage")
def delete_form(form_id):
    form = owned_form(form_id)
    banner_paths = [form.banner_image_path]
    if not form.parent_form_id:
        feedback_form = Form.query.filter_by(parent_form_id=form.id).first()
        if feedback_form:
            banner_paths.append(feedback_form.banner_image_path)
            db.session.delete(feedback_form)
    db.session.delete(form)
    db.session.commit()
    for banner_path in banner_paths:
        remove_banner(banner_path)
    flash("Form and its responses deleted.", "success")
    return redirect(url_for("main.dashboard"))


@forms.get("/forms/<int:form_id>/responses")
@login_required
@requires_permission("responses.view")
def responses(form_id):
    form = owned_form(form_id)
    submissions = Submission.query.filter_by(form_id=form.id).order_by(Submission.submitted_at.desc()).all()
    return render_template("forms/responses.html", form=form, submissions=submissions)


@forms.get("/forms/<int:form_id>/export.csv")
@forms.get("/forms/<int:form_id>/export-csv")
@login_required
@requires_permission("responses.export")
def export_csv(form_id):
    form = owned_form(form_id)
    questions = form.questions or []
    is_feedback_form = bool(form.parent_form_id)
    if is_feedback_form:
        question_ids = None
        header = [
            "name",
            "college_name",
            "email",
            "rating",
            "feedback",
            "submission_id",
            "submitted_at_utc",
        ]
    else:
        question_ids = [question["id"] for question in questions]
        header = ["Submission ID", "Submitted At (UTC)", "Respondent Email"] + [
            question.get("title", question["id"]) for question in questions
        ]
    target_form_id = form.id

    def rows():
        yield header
        submissions = db.session.scalars(
            db.select(Submission)
            .where(Submission.form_id == target_form_id)
            .order_by(Submission.submitted_at.desc(), Submission.id.desc())
            .execution_options(yield_per=500)
        )
        for submission in submissions:
            answers = submission.answers or {}
            submitted_at = submission.submitted_at.strftime("%Y-%m-%d %H:%M:%S")
            if is_feedback_form:
                yield [
                    answers.get("certificate_name"),
                    answers.get("college_name"),
                    answers.get("email") or submission.respondent_email,
                    answers.get("rating"),
                    answers.get("feedback"),
                    submission.id,
                    submitted_at,
                ]
            else:
                yield [
                    submission.id,
                    submitted_at,
                    submission.respondent_email,
                    *(answers.get(question_id) for question_id in question_ids),
                ]

    response = Response(
        stream_with_context(iter_csv_rows(rows())),
        content_type="text/csv; charset=utf-8",
    )
    filename = export_filename(form.id, form.title)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@forms.get("/forms/<int:form_id>/responses/<int:submission_id>")
@login_required
@requires_permission("responses.view")
def response_detail(form_id, submission_id):
    form = owned_form(form_id)
    submission = Submission.query.filter_by(id=submission_id, form_id=form.id).first_or_404()
    questions = {question["id"]: question["title"] for question in form.questions}
    return render_template(
        "forms/response_detail.html", form=form, submission=submission, questions=questions
    )


@forms.route("/f/<public_id>", methods=["GET", "POST"])
@limiter.limit("20 per minute", methods=["POST"])
def public_form(public_id):
    form = db.session.scalar(db.select(Form).where(Form.public_id == public_id))
    if not form or (not form.is_published and not form.closed_at):
        return render_template("errors/404.html"), 404
    if form.closed_at:
        return render_template(
            "forms/closed.html",
            form=form,
        )
    if request.method == "GET":
        return render_template("forms/public_form.html", form=form)
    try:
        answers, recipient = validate_answers(form.questions, request.form)
    except ValueError as error:
        flash(str(error), "error")
        return render_template("forms/public_form.html", form=form), 400
    submission = Submission(form_id=form.id, answers=answers, respondent_email=recipient)
    db.session.add(submission)
    db.session.commit()

    is_feedback_form = bool(form.parent_form_id)
    email_status = "skipped" if is_feedback_form else "not_configured"
    if recipient and not is_feedback_form:
        subject = form.confirmation_subject.replace("{email}", recipient)
        body = form.confirmation_body
        for question in form.questions:
            value = answers[question["id"]]
            replacement = ", ".join(value) if isinstance(value, list) else str(value)
            subject = subject.replace("{" + question["id"] + "}", replacement)
            body = body.replace("{" + question["id"] + "}", replacement)
        event_date = (
            form.event_start_at.strftime("%d %B %Y") if form.event_start_at else ""
        )
        event_time = ""
        if form.event_start_at:
            event_time = form.event_start_at.strftime("%I:%M %p")
            if form.event_end_at:
                event_time += f"–{form.event_end_at.strftime('%I:%M %p')}"
            event_time += f" {form.event_timezone}"
        for placeholder, value in {
            "event_date": event_date,
            "event_time": event_time,
            "event_location": form.event_location or "",
            "event_url": form.event_url or "",
        }.items():
            subject = subject.replace("{" + placeholder + "}", value)
            body = body.replace("{" + placeholder + "}", value)
        if "\r" in subject or "\n" in subject:
            subject = "Thanks for your response"
        body = body.replace("{email}", recipient)
        body = body.replace("{form_title}", form.title)
        try:
            email_args = (recipient, subject, body, form.organization)
            event_details = None
            calendar_event = None
            event_url = None
            if form.event_start_at or form.event_location or form.event_url:
                public_form_url = absolute_app_url(
                    "forms.public_form", public_id=form.public_id
                )
                event_url = form.event_url or public_form_url
                event_details = {
                    "location": form.event_location,
                    "timezone": form.event_timezone,
                }
                if form.event_start_at:
                    timezone = ZoneInfo(form.event_timezone)
                    event_details.update(
                        date=form.event_start_at.strftime("%A, %d %B %Y"),
                        start_time=form.event_start_at.strftime("%I:%M %p"),
                        end_time=(
                            form.event_end_at.strftime("%I:%M %p")
                            if form.event_end_at
                            else None
                        ),
                        timezone_label=form.event_start_at.replace(tzinfo=timezone).tzname(),
                    )
                    calendar_event = {
                        "uid": f"form-{form.id}@forms.gamkers",
                        "title": form.title,
                        "start": form.event_start_at,
                        "end": form.event_end_at
                        or form.event_start_at + timedelta(hours=1),
                        "timezone": form.event_timezone,
                        "location": form.event_location or "",
                        "description": form.description,
                        "url": event_url,
                    }
            if event_details:
                sent = send_email(
                    *email_args,
                    content_format=form.confirmation_format,
                    event_details=event_details,
                    event_title=form.title,
                    event_url=event_url,
                    calendar_event=calendar_event,
                )
            elif form.confirmation_format == "text":
                sent = send_email(*email_args)
            else:
                sent = send_email(*email_args, form.confirmation_format)
            email_status = "sent" if sent else "not_configured"
        except Exception:
            current_app.logger.exception("Form confirmation email delivery failed.")
            email_status = "failed"
    submission.email_status = email_status
    db.session.commit()
    return render_template("forms/submitted.html", email_status=email_status)


@forms.get("/f/<public_id>/banner")
def public_form_banner(public_id):
    form = db.session.scalar(db.select(Form).where(Form.public_id == public_id))
    if not form or (not form.is_published and not form.closed_at) or not form.banner_image_path:
        return render_template("errors/404.html"), 404
    root = Path(current_app.config["FORM_BANNER_STORAGE_PATH"]).resolve()
    image_path = (root / form.banner_image_path).resolve()
    if root not in image_path.parents or not image_path.is_file():
        return render_template("errors/404.html"), 404
    return send_file(image_path, max_age=3600, conditional=True)
