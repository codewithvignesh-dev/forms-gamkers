import csv
import io
import re
import smtplib
import ssl

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import limiter
from app.mail import send_email
from app.permissions import requires_permission
from app.security import valid_email

bulk_mail = Blueprint("bulk_mail", __name__, url_prefix="/mail")


def parse_recipient_csv(uploaded_file, max_recipients):
    try:
        content = uploaded_file.read().decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Upload a UTF-8 encoded CSV file.") from error
    try:
        reader = csv.DictReader(io.StringIO(content, newline=""), strict=True)
        raw_headers = reader.fieldnames or []
        if not raw_headers or any(header is None for header in raw_headers):
            raise ValueError("The CSV must have a header row with an email column.")
        headers = [header.strip().lower() for header in raw_headers]
        if not headers or "email" not in headers or len(headers) != len(set(headers)):
            raise ValueError("The CSV must have a unique header row containing an email column.")
        email_index = headers.index("email")
        name_index = headers.index("name") if "name" in headers else None
        recipients = []
        seen = set()
        invalid_count = 0
        duplicate_count = 0
        for row in reader:
            values = list(row.values())
            email = (values[email_index] or "").strip().lower() if email_index < len(values) else ""
            if not email:
                continue
            if not valid_email(email):
                invalid_count += 1
                continue
            if email in seen:
                duplicate_count += 1
                continue
            seen.add(email)
            name = (
                values[name_index].strip()
                if name_index is not None and name_index < len(values) and values[name_index]
                else ""
            )
            name = re.sub(r"[\x00-\x1f\x7f]+", " ", name).strip()
            recipients.append({"email": email, "name": name[:120]})
            if len(recipients) > max_recipients:
                raise ValueError(f"Upload no more than {max_recipients} unique recipients.")
    except csv.Error as error:
        raise ValueError("The CSV file could not be read.") from error
    if not recipients:
        raise ValueError("The CSV has no valid email addresses to send to.")
    return recipients, invalid_count, duplicate_count


@bulk_mail.route("/bulk", methods=["GET", "POST"])
@login_required
@requires_permission("bulk_mail.send")
@limiter.limit("3 per hour", methods=["POST"])
def compose():
    if request.method == "GET":
        return render_template("bulk_mail.html")

    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()
    uploaded_file = request.files.get("recipient_file")
    if not subject or len(subject) > 200 or "\r" in subject or "\n" in subject:
        flash("Enter a subject of up to 200 characters on one line.", "error")
        return render_template("bulk_mail.html"), 400
    if not body or len(body) > 10000:
        flash("Enter a message of up to 10,000 characters.", "error")
        return render_template("bulk_mail.html"), 400
    if not uploaded_file or not uploaded_file.filename:
        flash("Choose a CSV recipient file.", "error")
        return render_template("bulk_mail.html"), 400
    try:
        recipients, invalid_count, duplicate_count = parse_recipient_csv(
            uploaded_file, current_app.config["BULK_MAIL_MAX_RECIPIENTS"]
        )
    except ValueError as error:
        flash(str(error), "error")
        return render_template("bulk_mail.html"), 400

    sent_count = 0
    failed_count = 0
    for recipient in recipients:
        personalized_body = body.replace("{name}", recipient["name"])
        personalized_body = personalized_body.replace("{email}", recipient["email"])
        try:
            sent = send_email(
                recipient["email"],
                subject.replace("{name}", recipient["name"]),
                personalized_body,
                current_user.organization,
            )
        except (OSError, RuntimeError, ValueError, smtplib.SMTPException, ssl.SSLError):
            current_app.logger.exception("Bulk email delivery failed for a recipient.")
            sent = False
        if sent:
            sent_count += 1
        else:
            failed_count += 1

    flash(
        f"Bulk mail complete: {sent_count} sent, {failed_count} failed, "
        f"{invalid_count} invalid and {duplicate_count} duplicate rows skipped.",
        "success" if not failed_count else "error",
    )
    return redirect(url_for("bulk_mail.compose"))
