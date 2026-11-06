import io
import secrets
from datetime import timedelta
from pathlib import Path

import pandas as pd
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.certificates.models import CertificateBatch, CertificateRecord, CertificateUpload
from app.certificates.services import (
    create_certificate_batch,
    read_recipients,
)
from app.extensions import db, limiter
from app.forms.csv_export import iter_csv_rows, sanitize_csv_value
from app.forms.models import Form, Submission
from app.permissions import requires_permission
from app.security import valid_email
from app.urls import absolute_app_url
from app.utils import utcnow

certificates = Blueprint("certificates", __name__, url_prefix="/certificates")


def _storage_root():
    root = Path(current_app.config["CERTIFICATE_STORAGE_PATH"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _uploads_root():
    root = Path(current_app.config["CERTIFICATE_UPLOAD_PATH"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _remove_upload(upload):
    root = _uploads_root()
    path = (root / upload.stored_filename).resolve()
    if root in path.parents and path.is_file():
        path.unlink()
    db.session.delete(upload)


def _owned_batch(batch_id):
    return CertificateBatch.query.filter_by(
        id=batch_id, organization_id=current_user.organization_id
    ).first_or_404()


def _public_pdf_path(certificate):
    if (
        certificate.pdf_deleted_at
        or certificate.pdf_expires_at
        and certificate.pdf_expires_at <= utcnow()
    ):
        abort(410)
    if not certificate.pdf_path:
        return None
    root = _storage_root()
    pdf_path = (root / certificate.pdf_path).resolve()
    if root not in pdf_path.parents or not pdf_path.is_file():
        return None
    return pdf_path


@certificates.get("/")
@login_required
@requires_permission("certificates.manage")
def index():
    batches = (
        CertificateBatch.query.filter_by(organization_id=current_user.organization_id)
        .order_by(CertificateBatch.created_at.desc())
        .limit(100)
        .all()
    )
    return render_template("certificates/index.html", batches=batches)


@certificates.route("/forms/<int:form_id>/generate", methods=["GET", "POST"])
@login_required
@requires_permission("certificates.manage")
@limiter.limit("5 per hour", methods=["POST"])
def generate_from_feedback(form_id):
    event_form = Form.query.filter_by(
        id=form_id,
        organization_id=current_user.organization_id,
    ).first_or_404()
    if not event_form.closed_at or event_form.parent_form_id:
        flash("Close a registration form before issuing its feedback certificates.", "error")
        return redirect(url_for("forms.edit_form", form_id=event_form.id))
    feedback_form = Form.query.filter_by(parent_form_id=event_form.id).first_or_404()
    submissions = (
        Submission.query.filter_by(form_id=feedback_form.id)
        .order_by(Submission.submitted_at, Submission.id)
        .all()
    )
    issued_ids = set(
        db.session.scalars(
            db.select(CertificateRecord.submission_id).where(
                CertificateRecord.submission_id.in_(
                    [submission.id for submission in submissions] or [-1]
                )
            )
        ).all()
    )
    records = [
        {
            "submission_id": submission.id,
            "name": str((submission.answers or {}).get("certificate_name", "")).strip(),
            "college_name": str((submission.answers or {}).get("college_name", "")).strip(),
            "email": submission.respondent_email,
        }
        for submission in submissions
        if submission.id not in issued_ids
        and str((submission.answers or {}).get("certificate_name", "")).strip()
        and str((submission.answers or {}).get("college_name", "")).strip()
        and valid_email(submission.respondent_email or "")
    ]
    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        if not title or len(title) > 180 or not description or len(description) > 5000:
            error = "Enter a certificate title and a description under 5,000 characters."
        elif not records:
            error = "No eligible feedback responses are available for certificate generation."
        elif len(records) > current_app.config["CERTIFICATE_MAX_RECORDS"]:
            error = (
                f"Generate at most {current_app.config['CERTIFICATE_MAX_RECORDS']} "
                "certificates per batch."
            )
        else:
            batch = create_certificate_batch(
                organization_id=current_user.organization_id,
                creator_id=current_user.id,
                source_form_id=feedback_form.id,
                title=title,
                description=description,
                records=records,
            )
            flash(
                f"{batch.generated_records} certificates generated. "
                f"{batch.failed_records} failed.",
                "success" if not batch.failed_records else "error",
            )
            return redirect(url_for("certificates.batch_detail", batch_id=batch.id))
        flash(error, "error")
    return render_template(
        "certificates/generate_feedback.html",
        event_form=event_form,
        feedback_form=feedback_form,
        eligible_count=len(records),
        issued_count=len(issued_ids),
        default_title=event_form.title,
    )


@certificates.post("/upload")
@login_required
@requires_permission("certificates.manage")
@limiter.limit("5 per hour")
def upload_recipients():
    uploaded_file = request.files.get("recipient_file")
    if not uploaded_file or not uploaded_file.filename:
        flash("Choose a CSV, XLSX or XLS recipient file.", "error")
        return redirect(url_for("certificates.index"))
    original_filename = secure_filename(Path(uploaded_file.filename).name)
    if not original_filename:
        flash("The uploaded filename is invalid.", "error")
        return redirect(url_for("certificates.index"))
    try:
        recipients = read_recipients(uploaded_file, original_filename)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("certificates.index"))
    uploads = _uploads_root()
    staged_name = secrets.token_hex(24) + Path(original_filename).suffix.lower()
    staged_path = (uploads / staged_name).resolve()
    if uploads not in staged_path.parents:
        raise RuntimeError("The uploaded certificate file escaped the private storage directory.")
    uploaded_file.stream.seek(0)
    uploaded_file.save(staged_path)

    expired = CertificateUpload.query.filter(
        CertificateUpload.organization_id == current_user.organization_id,
        CertificateUpload.expires_at <= utcnow(),
    ).limit(25).all()
    for old_upload in expired:
        _remove_upload(old_upload)
    staged = CertificateUpload(
        organization_id=current_user.organization_id,
        creator_id=current_user.id,
        stored_filename=staged_name,
        original_filename=original_filename,
        total_records=len(recipients),
        preview_records=recipients[:3],
        expires_at=utcnow() + timedelta(hours=2),
    )
    try:
        db.session.add(staged)
        db.session.commit()
    except Exception:
        staged_path.unlink(missing_ok=True)
        db.session.rollback()
        raise
    return redirect(url_for("certificates.preview_upload", upload_id=staged.id))


@certificates.get("/uploads/<int:upload_id>")
@login_required
@requires_permission("certificates.manage")
def preview_upload(upload_id):
    staged = CertificateUpload.query.filter_by(
        id=upload_id, organization_id=current_user.organization_id
    ).first_or_404()
    if staged.expires_at <= utcnow():
        _remove_upload(staged)
        db.session.commit()
        flash("This upload preview expired. Please upload the file again.", "error")
        return redirect(url_for("certificates.index"))
    return render_template("certificates/preview_upload.html", staged=staged)


@certificates.post("/uploads/<int:upload_id>/generate")
@login_required
@requires_permission("certificates.manage")
@limiter.limit("5 per hour")
def generate_from_upload(upload_id):
    staged = CertificateUpload.query.filter_by(
        id=upload_id, organization_id=current_user.organization_id
    ).first_or_404()
    if staged.expires_at <= utcnow():
        _remove_upload(staged)
        db.session.commit()
        flash("This upload preview expired. Please upload the file again.", "error")
        return redirect(url_for("certificates.index"))
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    if not title or len(title) > 180 or not description or len(description) > 5000:
        flash("Enter a certificate title and a description under 5,000 characters.", "error")
        return redirect(url_for("certificates.preview_upload", upload_id=staged.id))
    upload_path = (_uploads_root() / staged.stored_filename).resolve()
    if not upload_path.is_file():
        _remove_upload(staged)
        db.session.commit()
        flash("The temporary upload could not be found. Please upload it again.", "error")
        return redirect(url_for("certificates.index"))
    try:
        records = read_recipients(upload_path, staged.original_filename)
    except ValueError as error:
        _remove_upload(staged)
        db.session.commit()
        flash(str(error), "error")
        return redirect(url_for("certificates.index"))
    source_filename = staged.original_filename
    _remove_upload(staged)
    db.session.commit()
    batch = create_certificate_batch(
        organization_id=current_user.organization_id,
        creator_id=current_user.id,
        title=title,
        description=description,
        records=records,
        source_filename=source_filename,
    )
    flash(
        f"{batch.generated_records} certificates generated. {batch.failed_records} failed.",
        "success" if not batch.failed_records else "error",
    )
    return redirect(url_for("certificates.batch_detail", batch_id=batch.id))


@certificates.get("/batches/<int:batch_id>")
@login_required
@requires_permission("certificates.manage")
def batch_detail(batch_id):
    batch = _owned_batch(batch_id)
    records = CertificateRecord.query.filter_by(batch_id=batch.id).order_by(
        CertificateRecord.id
    ).all()
    return render_template(
        "certificates/batch_detail.html", batch=batch, certificates=records
    )


def _export_batch(batch_id, export_format):
    batch = _owned_batch(batch_id)
    rows = CertificateRecord.query.filter_by(batch_id=batch.id).order_by(
        CertificateRecord.id
    ).all()
    headers = [
        "Name",
        "College Name",
        "Certificate Number",
        "Status",
        "Certificate Link",
        "Email Delivery",
    ]
    records = [
        [
            record.recipient_name,
            record.college_name,
            record.certificate_number,
            record.status,
            (
                absolute_app_url("certificates.public_certificate", token=record.public_token)
                if record.status == "generated"
                else ""
            ),
            record.notification_status,
        ]
        for record in rows
    ]
    if export_format == "csv":
        response = current_app.response_class(
            iter_csv_rows([headers, *records]),
            content_type="text/csv; charset=utf-8",
        )
        filename = secure_filename(batch.title) or "certificates"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{filename}_certificates.csv"'
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    safe_records = [
        [sanitize_csv_value(value) for value in row]
        for row in records
    ]
    buffer = io.BytesIO()
    dataframe = pd.DataFrame(safe_records, columns=headers)
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        dataframe.to_excel(writer, index=False, sheet_name="Certificates")
    buffer.seek(0)
    filename = secure_filename(batch.title) or "certificates"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"{filename}_certificates.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        max_age=0,
    )


@certificates.get("/batches/<int:batch_id>/export.csv")
@login_required
@requires_permission("certificates.manage")
def export_batch_csv(batch_id):
    return _export_batch(batch_id, "csv")


@certificates.get("/batches/<int:batch_id>/export.xlsx")
@login_required
@requires_permission("certificates.manage")
def export_batch_xlsx(batch_id):
    return _export_batch(batch_id, "xlsx")


@certificates.get("/public/<token>")
def public_certificate(token):
    certificate = CertificateRecord.query.filter_by(
        public_token=token, status="generated"
    ).first_or_404()
    batch = db.session.get(CertificateBatch, certificate.batch_id)
    return render_template(
        "certificates/public.html",
        certificate=certificate,
        batch=batch,
        pdf_available=(
            not certificate.pdf_deleted_at
            and (
                certificate.pdf_expires_at is None
                or certificate.pdf_expires_at > utcnow()
            )
        ),
    )


@certificates.get("/verify")
@limiter.limit("30 per minute")
def verify_certificate():
    certificate_number = request.args.get("number", "").strip()
    certificate = None
    batch = None
    if certificate_number and len(certificate_number) <= 48:
        certificate = CertificateRecord.query.filter_by(
            certificate_number=certificate_number,
            status="generated",
        ).first()
        if certificate:
            batch = db.session.get(CertificateBatch, certificate.batch_id)
    return render_template(
        "certificates/verify.html",
        certificate_number=certificate_number,
        certificate=certificate,
        batch=batch,
        searched=bool(certificate_number),
    )


@certificates.get("/public/<token>/pdf")
def public_certificate_pdf(token):
    certificate = CertificateRecord.query.filter_by(
        public_token=token, status="generated"
    ).first_or_404()
    pdf_path = _public_pdf_path(certificate)
    if pdf_path is None:
        return render_template("errors/404.html"), 404
    response = send_file(
        pdf_path,
        as_attachment=False,
        download_name=f"{certificate.certificate_number}.pdf",
        mimetype="application/pdf",
        max_age=0,
    )
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; "
        "img-src 'self' data:; form-action 'self'; frame-ancestors 'self'; "
        "base-uri 'self'; object-src 'self'"
    )
    return response


@certificates.get("/public/<token>/download")
def download_public_certificate(token):
    certificate = CertificateRecord.query.filter_by(
        public_token=token, status="generated"
    ).first_or_404()
    pdf_path = _public_pdf_path(certificate)
    if pdf_path is None:
        return render_template("errors/404.html"), 404
    return send_file(
        pdf_path,
        as_attachment=True,
        download_name=f"{certificate.certificate_number}.pdf",
        mimetype="application/pdf",
        max_age=0,
    )
