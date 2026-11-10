from html import escape
from pathlib import Path
import logging
import zipfile
from datetime import timedelta

import pandas as pd
from flask import current_app

from app.certificates.generator import (
    generate_certificate_number,
    generate_pdf,
    generate_public_token,
    render_description,
)
from app.certificates.models import CertificateBatch, CertificateRecord, CertificateUpload
from app.extensions import db
from app.mail import send_email
from app.organization.models import Organization
from app.security import valid_email
from app.urls import absolute_app_url
from app.utils import utcnow

logger = logging.getLogger(__name__)

NAME_COLUMNS = {"name", "student_name", "recipient_name", "certificate_name"}
COLLEGE_COLUMNS = {
    "college_name",
    "college",
    "institution",
    "institution_name",
    "organisation",
    "organisation_name",
    "organization",
    "organization_name",
}
EMAIL_COLUMNS = {"email", "email_address", "recipient_email"}
SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}


def _normalise_column(value):
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _find_column(columns, candidates):
    normalized = {_normalise_column(column): column for column in columns}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def read_recipients(file_or_path, filename):
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Upload a CSV, XLSX or XLS recipient list.")
    try:
        if extension == ".csv":
            dataframe = pd.read_csv(file_or_path, nrows=current_app.config["CERTIFICATE_MAX_RECORDS"] + 1)
        else:
            if extension == ".xlsx":
                with zipfile.ZipFile(file_or_path) as archive:
                    total_uncompressed = sum(info.file_size for info in archive.infolist())
                    if len(archive.infolist()) > 256 or total_uncompressed > 32 * 1024 * 1024:
                        raise ValueError("The uploaded spreadsheet exceeds safe size limits.")
                if hasattr(file_or_path, "seek"):
                    file_or_path.seek(0)
            dataframe = pd.read_excel(
                file_or_path, nrows=current_app.config["CERTIFICATE_MAX_RECORDS"] + 1
            )
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("The uploaded recipient file could not be read.") from error
    if dataframe.empty:
        raise ValueError("The uploaded file contains no recipient records.")
    if len(dataframe) > current_app.config["CERTIFICATE_MAX_RECORDS"]:
        raise ValueError(
            f"Generate at most {current_app.config['CERTIFICATE_MAX_RECORDS']} certificates per batch."
        )
    name_column = _find_column(dataframe.columns, NAME_COLUMNS)
    college_column = _find_column(dataframe.columns, COLLEGE_COLUMNS)
    email_column = _find_column(dataframe.columns, EMAIL_COLUMNS)
    if name_column is None or college_column is None:
        raise ValueError(
            "The file must include a name column and a college_name or institution column."
        )

    records = []
    for row in dataframe.to_dict(orient="records"):
        name_value = row.get(name_column)
        college_value = row.get(college_column)
        email_value = row.get(email_column) if email_column else None
        name = "" if pd.isna(name_value) else str(name_value).strip()
        college = "" if pd.isna(college_value) else str(college_value).strip()
        email = (
            "" if email_value is None or pd.isna(email_value) else str(email_value).strip()
        )
        if name and college:
            if len(name) > 180 or len(college) > 180 or len(email) > 255:
                raise ValueError("Recipient names, institutions or email addresses are too long.")
            records.append(
                {
                    "name": name,
                    "college_name": college,
                    "email": email or None,
                }
            )
    if not records:
        raise ValueError("No rows contain both a recipient name and institution.")
    return records


def create_certificate_batch(
    *,
    organization_id,
    creator_id,
    title,
    description,
    records,
    source_form_id=None,
    source_filename=None,
):
    if not records or len(records) > current_app.config["CERTIFICATE_MAX_RECORDS"]:
        raise ValueError("The certificate batch has no recipients or exceeds the batch limit.")
    batch = CertificateBatch(
        organization_id=organization_id,
        creator_id=creator_id,
        source_form_id=source_form_id,
        title=title,
        description=description,
        source_filename=source_filename,
        total_records=len(records),
        status="processing",
    )
    db.session.add(batch)
    db.session.commit()

    storage_root = Path(current_app.config["CERTIFICATE_STORAGE_PATH"]).resolve()
    generated = failed = 0
    for item in records:
        token = generate_public_token()
        certificate = CertificateRecord(
            batch_id=batch.id,
            submission_id=item.get("submission_id"),
            recipient_name=item["name"],
            college_name=item["college_name"],
            recipient_email=item.get("email"),
            certificate_number=f"PENDING-{token[:20]}",
            public_token=token,
            status="pending",
        )
        db.session.add(certificate)
        db.session.flush()
        certificate.certificate_number = generate_certificate_number(certificate.id)
        certificate.pdf_expires_at = certificate.created_at + timedelta(
            days=current_app.config["CERTIFICATE_PDF_RETENTION_DAYS"]
        )

        relative_path = Path(str(organization_id), str(batch.id), f"{token[:24]}.pdf")
        pdf_path = (storage_root / relative_path).resolve()
        if storage_root not in pdf_path.parents:
            raise RuntimeError("The generated certificate path escaped the private storage directory.")
        issue_date_str = certificate.created_at.strftime("%d %B %Y")
        rendered_desc = render_description(
            description=description,
            name=item["name"],
            college_name=item["college_name"],
            certificate_number=certificate.certificate_number,
            date=issue_date_str,
        )
        certificate.title = title
        certificate.description = description
        certificate.rendered_content = rendered_desc
        certificate.issue_date = issue_date_str
        try:
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            generate_pdf(
                title=title,
                description=description,
                name=item["name"],
                college_name=item["college_name"],
                certificate_number=certificate.certificate_number,
                output_path=pdf_path,
                issue_date=issue_date_str,
            )
            certificate.pdf_path = relative_path.as_posix()
            certificate.status = "generated"
            generated += 1
        except Exception:
            current_app.logger.exception(
                "Certificate PDF generation failed for certificate id=%s.", certificate.id
            )
            pdf_path.unlink(missing_ok=True)
            certificate.status = "failed"
            failed += 1
        db.session.commit()
        if certificate.status == "generated":
            certificate.notification_status = _send_certificate_link(
                certificate, title, organization_id
            )
            db.session.commit()

    batch.generated_records = generated
    batch.failed_records = failed
    batch.status = "completed" if not failed else "completed_with_errors"
    batch.completed_at = utcnow()
    db.session.commit()
    return batch


def _send_certificate_link(certificate, batch_title, organization_id):
    email = certificate.recipient_email
    if not email or not valid_email(email):
        return "skipped"
    try:
        public_url = absolute_app_url(
            "certificates.public_certificate", token=certificate.public_token
        )
        body = (
            f"<p>Hello {escape(certificate.recipient_name)},</p>"
            f"<p>Your certificate for <strong>{escape(batch_title)}</strong> is ready.</p>"
            f"<p>Certificate number: <strong>{escape(certificate.certificate_number)}</strong></p>"
            f'<p><a href="{escape(public_url, quote=True)}">View and download your certificate</a></p>'
            f"<p>Your certificate details remain available for verification. The PDF can be downloaded for {current_app.config['CERTIFICATE_PDF_RETENTION_DAYS']} days after issue.</p>"
        )
        organization = db.session.get(Organization, organization_id)
        sent = send_email(
            email,
            f"Your certificate is ready — {batch_title}",
            body,
            organization,
            "html",
        )
        return "sent" if sent else "not_configured"
    except Exception:
        current_app.logger.exception(
            "Certificate notification delivery failed for certificate id=%s.",
            certificate.id,
        )
        return "failed"


def cleanup_expired_certificate_pdfs(limit=500):
    now = utcnow()
    root = Path(current_app.config["CERTIFICATE_STORAGE_PATH"]).resolve()
    expired = db.session.scalars(
        db.select(CertificateRecord)
        .where(
            CertificateRecord.status == "generated",
            CertificateRecord.pdf_path.is_not(None),
            CertificateRecord.pdf_expires_at <= now,
            CertificateRecord.pdf_deleted_at.is_(None),
        )
        .order_by(CertificateRecord.pdf_expires_at)
        .limit(limit)
    ).all()
    removed = 0
    for certificate in expired:
        path = (root / certificate.pdf_path).resolve()
        if root not in path.parents:
            logger.error(
                "Refusing to delete certificate PDF outside storage root for certificate id=%s.",
                certificate.id,
            )
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception(
                "Could not delete expired certificate PDF for certificate id=%s.",
                certificate.id,
            )
            continue
        certificate.pdf_deleted_at = now
        removed += 1

    upload_root = Path(current_app.config["CERTIFICATE_UPLOAD_PATH"]).resolve()
    expired_uploads = db.session.scalars(
        db.select(CertificateUpload)
        .where(CertificateUpload.expires_at <= now)
        .limit(limit)
    ).all()
    for staged in expired_uploads:
        if staged.stored_filename:
            upload_path = (upload_root / staged.stored_filename).resolve()
            if upload_root in upload_path.parents and upload_path.is_file():
                try:
                    upload_path.unlink()
                except OSError:
                    logger.exception("Could not unlink expired staged upload file %s.", staged.id)
        db.session.delete(staged)

    db.session.commit()
    return removed
