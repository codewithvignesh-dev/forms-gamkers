import csv
import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from app.certificates.services import create_certificate_batch, read_recipients
from app.extensions import db
from app.mail import _calendar_invite, _calendar_url, _styled_email, send_email
from app.models import CertificateBatch, CertificateRecord, Form, Submission, User
from app.utils import utcnow


def _register(client):
    result = client.application.test_cli_runner().invoke(
        args=[
            "create-admin",
            "--name",
            "Event Admin",
            "--email",
            "event-admin@example.com",
        ],
        input="safe-test-password-2026\nsafe-test-password-2026\n",
    )
    assert result.exit_code == 0, result.output
    return client.post(
        "/login",
        data={"email": "event-admin@example.com", "password": "safe-test-password-2026"},
        follow_redirects=True,
    )


def _published_event(client):
    questions = [
        {
            "id": "email",
            "title": "Email",
            "type": "email",
            "required": True,
            "options": [],
        },
        {
            "id": "name",
            "title": "Name",
            "type": "short_text",
            "required": True,
            "options": [],
        },
    ]
    client.post(
        "/forms/new",
        data={
            "title": "Security Summit",
            "questions": json.dumps(questions),
            "confirmation_subject": "You're registered for {form_title}",
            "confirmation_body": "Hello {name}",
            "confirmation_format": "markdown",
            "publish": "yes",
        },
    )
    return Form.query.filter_by(title="Security Summit").first()


def test_application_requires_mysql_connection_settings(monkeypatch):
    from app import create_app
    from config import Config

    monkeypatch.setattr(Config, "SECRET_KEY", "test-secret")
    monkeypatch.setattr(Config, "SQLALCHEMY_DATABASE_URI", None)
    monkeypatch.setattr(Config, "DB_HOST", None)
    monkeypatch.setattr(Config, "DB_PORT", "3306")
    monkeypatch.setattr(Config, "DB_NAME", None)
    monkeypatch.setattr(Config, "DB_USER", None)
    monkeypatch.setattr(Config, "DB_PASSWORD", None)
    with pytest.raises(RuntimeError, match="DB_HOST"):
        create_app()


def test_mysql_connection_settings_escape_password_special_characters():
    from app import create_app
    from sqlalchemy.engine import make_url

    app = create_app(
        {
            "SECRET_KEY": "test-secret",
            "DB_HOST": "db.example",
            "DB_PORT": "3307",
            "DB_NAME": "forms_db",
            "DB_USER": "forms_user",
            "DB_PASSWORD": "safe:@/password?#",
        }
    )
    database_url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
    assert database_url.drivername == "mysql+pymysql"
    assert database_url.host == "db.example"
    assert database_url.port == 3307
    assert database_url.database == "forms_db"
    assert database_url.username == "forms_user"
    assert database_url.password == "safe:@/password?#"
    assert database_url.query["charset"] == "utf8mb4"


def test_wsgi_entrypoints_start_the_flask_application(monkeypatch):
    import runpy

    import app as app_package
    from flask import Flask

    original_create_app = app_package.create_app
    created_apps = []
    started_apps = []

    def create_test_app():
        app = original_create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "wsgi-smoke-test",
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
            }
        )
        created_apps.append(app)
        return app

    monkeypatch.setattr(app_package, "create_app", create_test_app)
    monkeypatch.setattr(Flask, "run", lambda application: started_apps.append(application))

    runpy.run_path("wsgi.py", run_name="__main__")
    runpy.run_path("passenger_wsgi.py", run_name="__main__")

    assert len(created_apps) == 2
    assert started_apps == created_apps


def test_published_forms_save_changes_without_republishing_or_unpublishing(client):
    _register(client)
    event_form = _published_event(client)

    editor = client.get(f"/forms/{event_form.id}/edit")
    assert b"Save changes" in editor.data
    assert b"Publish form" not in editor.data

    questions = [
        {
            "id": "email",
            "title": "Email",
            "type": "email",
            "required": True,
            "options": [],
        },
        {
            "id": "name",
            "title": "Name",
            "type": "short_text",
            "required": True,
            "options": [],
        },
    ]
    response = client.post(
        f"/forms/{event_form.id}/edit",
        data={
            "title": "Security Summit Updated",
            "questions": json.dumps(questions),
            "confirmation_subject": "Thanks",
            "confirmation_body": "Thanks {name}",
            "confirmation_format": "markdown",
        },
    )
    assert response.status_code == 302
    db.session.refresh(event_form)
    assert event_form.is_published
    assert event_form.title == "Security Summit Updated"


def test_form_banner_upload_is_validated_private_and_served_with_public_form(
    app, client, tmp_path
):
    from PIL import Image

    _register(client)
    event_form = _published_event(client)
    app.config["FORM_BANNER_STORAGE_PATH"] = str(tmp_path / "private-banners")
    image = io.BytesIO()
    Image.new("RGB", (640, 240), "#234567").save(image, format="PNG")

    response = client.post(
        f"/forms/{event_form.id}/edit",
        data={
            "title": event_form.title,
            "description": "Banner test",
            "questions": json.dumps(event_form.questions),
            "confirmation_subject": "Thanks",
            "confirmation_body": "Thanks {name}",
            "confirmation_format": "text",
            "banner_image": (io.BytesIO(image.getvalue()), "event.png"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    db.session.refresh(event_form)
    banner_path = Path(app.config["FORM_BANNER_STORAGE_PATH"]) / event_form.banner_image_path
    assert banner_path.is_file()
    public_page = client.get(f"/f/{event_form.public_id}")
    assert b"public_form_banner" not in public_page.data
    assert f"/f/{event_form.public_id}/banner".encode() in public_page.data
    banner = client.get(f"/f/{event_form.public_id}/banner")
    assert banner.status_code == 200
    assert banner.mimetype == "image/png"
    assert banner.data.startswith(b"\x89PNG")

    response = client.post(
        f"/forms/{event_form.id}/edit",
        data={
            "title": event_form.title,
            "questions": json.dumps(event_form.questions),
            "confirmation_subject": "Thanks",
            "confirmation_body": "Thanks {name}",
            "confirmation_format": "text",
            "banner_image": (io.BytesIO(b"not an image"), "fake.png"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert b"valid image" in response.data
    assert banner_path.is_file()


def test_closing_registration_creates_feedback_form_and_generates_permanent_certificates(
    app, client, tmp_path, monkeypatch
):
    app.config["CERTIFICATE_STORAGE_PATH"] = str(tmp_path / "private-certificates")
    mailed = []
    feedback_mail = []
    monkeypatch.setattr(
        "app.certificates.services.send_email",
        lambda *args: mailed.append(args) or True,
    )
    monkeypatch.setattr(
        "app.forms.routes.send_email",
        lambda *args, **kwargs: feedback_mail.append((args, kwargs)) or True,
    )
    _register(client)
    event_form = _published_event(client)
    response = client.post(
        f"/forms/{event_form.id}/close",
        data={"closed_message": "Registration is closed. Thank you."},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Copy link" in response.data

    feedback_form = Form.query.filter_by(parent_form_id=event_form.id).one()
    assert feedback_form.is_published
    assert len(feedback_form.questions) == 5

    closed_page = client.get(f"/f/{event_form.public_id}")
    assert b"Registration is closed. Thank you." in closed_page.data
    assert b"Share feedback" not in closed_page.data
    assert feedback_form.public_id.encode() not in closed_page.data
    closed_post = client.post(
        f"/f/{event_form.public_id}",
        data={"name": "late attendee", "email": "late@example.com"},
    )
    assert b"Registration is closed" in closed_post.data
    assert Submission.query.filter_by(form_id=event_form.id).count() == 0

    response = client.post(
        f"/f/{feedback_form.public_id}",
        data={
            "certificate_name": "Alex Attendee",
            "college_name": "Northstar Institute",
            "email": "alex@example.com",
            "rating": "Excellent",
            "feedback": "Great workshop!",
        },
    )
    assert response.status_code == 200
    feedback_submission = Submission.query.filter_by(form_id=feedback_form.id).one()
    assert feedback_submission.email_status == "skipped"
    assert feedback_mail == []
    feedback_csv = client.get(f"/forms/{feedback_form.id}/export.csv")
    rows = list(
        csv.DictReader(io.StringIO(feedback_csv.data.decode("utf-8-sig")))
    )
    assert rows[0]["name"] == "Alex Attendee"
    assert rows[0]["college_name"] == "Northstar Institute"
    assert rows[0]["email"] == "alex@example.com"
    assert rows[0]["rating"] == "Excellent"
    assert read_recipients(
        io.BytesIO(feedback_csv.data), f"feedback_{feedback_form.id}.csv"
    )[0] == {
        "name": "Alex Attendee",
        "college_name": "Northstar Institute",
        "email": "alex@example.com",
    }

    response = client.post(
        f"/certificates/forms/{event_form.id}/generate",
        data={
            "title": "Certificate of Participation",
            "description": "Presented to {name} from {college_name}. No. {certificate_number}.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    certificate = CertificateRecord.query.one()
    batch = CertificateBatch.query.one()
    assert batch.generated_records == 1
    assert certificate.recipient_name == "Alex Attendee"
    assert certificate.notification_status == "sent"
    assert len(mailed) == 1
    assert mailed[0][0] == "alex@example.com"
    assert certificate.public_token in mailed[0][2]
    assert certificate.pdf_path
    pdf_path = Path(app.config["CERTIFICATE_STORAGE_PATH"]) / certificate.pdf_path
    assert pdf_path.is_file()
    assert pdf_path.read_bytes().startswith(b"%PDF")
    assert b"/Subtype /Image" not in pdf_path.read_bytes()

    public_page = client.get(f"/certificates/public/{certificate.public_token}")
    assert b"Alex Attendee" in public_page.data
    assert (
        f"/certificates/public/{certificate.public_token}/pdf".encode()
        in public_page.data
    )
    assert b"<iframe" in public_page.data
    public_pdf = client.get(f"/certificates/public/{certificate.public_token}/pdf")
    assert public_pdf.status_code == 200
    assert public_pdf.data.startswith(b"%PDF")
    assert public_pdf.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in public_pdf.headers["Content-Security-Policy"]
    downloaded_pdf = client.get(
        f"/certificates/public/{certificate.public_token}/download"
    )
    assert downloaded_pdf.status_code == 200
    assert downloaded_pdf.data == public_pdf.data == pdf_path.read_bytes()
    public_pdf.close()
    downloaded_pdf.close()

    assert certificate.title == "Certificate of Participation"
    assert certificate.description == "Presented to {name} from {college_name}. No. {certificate_number}."
    assert (
        certificate.rendered_content
        == f"Presented to Alex Attendee from Northstar Institute. No. {certificate.certificate_number}."
    )
    assert certificate.issue_date is not None

    verified = client.get(
        f"/certificates/verify?number={certificate.certificate_number}"
    )
    assert b"AUTHENTIC CERTIFICATE" in verified.data
    assert b"Alex Attendee" in verified.data
    assert (
        f"Presented to Alex Attendee from Northstar Institute. No. {certificate.certificate_number}.".encode()
        in verified.data
    )
    assert f"/certificates/verify/preview/{certificate.certificate_number}".encode() in verified.data
    assert f"/certificates/verify/download/{certificate.certificate_number}".encode() in verified.data

    session_preview = client.get(
        f"/certificates/verify/preview/{certificate.certificate_number}"
    )
    assert session_preview.status_code == 200
    assert session_preview.data.startswith(b"%PDF")
    session_download = client.get(
        f"/certificates/verify/download/{certificate.certificate_number}"
    )
    assert session_download.status_code == 200
    assert session_download.data.startswith(b"%PDF")

    other_client = client.application.test_client()
    unverified_preview = other_client.get(
        f"/certificates/verify/preview/{certificate.certificate_number}"
    )
    assert unverified_preview.status_code == 403
    unverified_download = other_client.get(
        f"/certificates/verify/download/{certificate.certificate_number}"
    )
    assert unverified_download.status_code == 403

    certificate.pdf_expires_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    cleanup = client.application.test_cli_runner().invoke(
        args=["cleanup-expired-certificate-pdfs"]
    )
    assert cleanup.exit_code == 0, cleanup.output
    assert "Deleted 1 expired certificate PDF" in cleanup.output
    assert not pdf_path.exists()
    assert certificate.pdf_deleted_at is not None
    verified_after = client.get(
        f"/certificates/verify?number={certificate.certificate_number}"
    )
    assert verified_after.status_code == 200
    assert b"Alex Attendee" in verified_after.data
    assert (
        f"Presented to Alex Attendee from Northstar Institute. No. {certificate.certificate_number}.".encode()
        in verified_after.data
    )
    assert client.get(
        f"/certificates/public/{certificate.public_token}/pdf"
    ).status_code == 410

    # Even after disk PDF is removed, verified session dynamically generates preview and download
    preview_after = client.get(
        f"/certificates/verify/preview/{certificate.certificate_number}"
    )
    assert preview_after.status_code == 200
    assert preview_after.data.startswith(b"%PDF")
    download_after = client.get(
        f"/certificates/verify/download/{certificate.certificate_number}"
    )
    assert download_after.status_code == 200
    assert download_after.data.startswith(b"%PDF")

    second_batch = client.post(
        f"/certificates/forms/{event_form.id}/generate",
        data={"title": "Duplicate", "description": "No duplicate."},
        follow_redirects=True,
    )
    assert b"No eligible feedback responses" in second_batch.data
    assert CertificateRecord.query.count() == 1


def test_uploaded_recipient_preview_generation_and_exports_are_tenant_scoped(
    app, client, tmp_path, monkeypatch
):
    app.config["CERTIFICATE_STORAGE_PATH"] = str(tmp_path / "private-certificates")
    monkeypatch.setattr("app.certificates.services.send_email", lambda *args: False)
    _register(client)
    response = client.post(
        "/certificates/upload",
        data={
            "recipient_file": (
                io.BytesIO(
                    b"name,college_name,email\nTaylor Example,Northstar Institute,taylor@example.com\n"
                ),
                "recipients.csv",
            )
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    preview_url = urlparse(response.headers["Location"]).path
    preview = client.get(preview_url)
    assert b"Taylor Example" in preview.data
    upload_id = preview_url.rsplit("/", 1)[-1]

    response = client.post(
        f"/certificates/uploads/{upload_id}/generate",
        data={
            "title": "Workshop certificate",
            "description": "Awarded to {name} at {college_name}.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    batch = CertificateBatch.query.one()
    assert batch.source_filename == "recipients.csv"
    record = CertificateRecord.query.one()
    assert record.recipient_email == "taylor@example.com"
    assert record.notification_status == "not_configured"
    assert CertificateBatch.query.count() == 1

    csv_export = client.get(f"/certificates/batches/{batch.id}/export.csv")
    assert csv_export.status_code == 200
    assert b"taylor" not in csv_export.data
    assert b"/certificates/public/" in csv_export.data
    xlsx_export = client.get(f"/certificates/batches/{batch.id}/export.xlsx")
    assert xlsx_export.status_code == 200
    assert xlsx_export.mimetype == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )



def test_recipient_reader_accepts_csv_and_rejects_missing_institution(app):
    with app.app_context():
        good = io.BytesIO(b"Student Name,Institution Name\nTaylor Example,Northstar Institute\n")
        records = read_recipients(good, "participants.csv")
        assert records == [
            {
                "name": "Taylor Example",
                "college_name": "Northstar Institute",
                "email": None,
            }
        ]
        bad = io.BytesIO(b"name,email\nTaylor Example,taylor@example.com\n")
        try:
            read_recipients(bad, "participants.csv")
        except ValueError as error:
            assert "institution column" in str(error)
        else:
            raise AssertionError("A recipient file without an institution must be rejected.")


def test_confirmation_html_and_markdown_are_sanitised_and_tailwind_styled(app):
    with app.app_context():
        organisation = type(
            "Organisation",
            (),
            {
                "name": "Northstar Events",
                "smtp_host": "mail.example.com",
                "smtp_port": 587,
                "smtp_username": None,
                "smtp_password_encrypted": None,
                "smtp_sender": "no-reply@example.com",
                "smtp_security": "starttls",
            },
        )()
        message, settings, _context = _styled_email(
            "alex@example.com",
            "Thanks",
            "<p>Hello <strong>Alex</strong></p><img src=x onerror=alert(1)>",
            organisation,
            "html",
        )
        assert settings["host"] == "mail.example.com"
        assert message.is_multipart()
        assert message.get_body(preferencelist=("plain",)) is None
        html_part = message.get_body(preferencelist=("html",)).get_content()
        assert "<strong>Alex</strong>" in html_part
        assert "cid:gamkers-portal-logo" in html_part
        assert "GAMKERS PORTAL" in html_part
        assert "Thanks" in html_part
        assert "onerror" not in html_part
        assert "style=" in html_part
        assert "overflow-wrap" in html_part
        assert "word-wrap" not in html_part
        assert "var(--" not in html_part
        logo = next(
            part for part in message.iter_attachments()
            if part.get_content_type() == "image/png"
        )
        assert logo["Content-ID"] == "<gamkers-portal-logo>"
        assert logo.get_content_disposition() == "inline"
        assert len(logo.get_content()) <= 100_000

        message, _, _ = _styled_email(
            "alex@example.com",
            "Thanks",
            "**Hello Alex**\n\nWelcome to [the event](javascript:alert(1)).",
            organisation,
            "markdown",
        )
        html_part = message.get_body(preferencelist=("html",)).get_content()
        assert "<strong>Hello Alex</strong>" in html_part
        assert "javascript:" not in html_part


def test_registration_confirmation_includes_event_details_and_calendar_links(
    app, client, monkeypatch
):
    _register(client)
    questions = [
        {
            "id": "email",
            "title": "Email",
            "type": "email",
            "required": True,
            "options": [],
        }
    ]
    response = client.post(
        "/forms/new",
        data={
            "title": "Modern Web Summit",
            "description": "A full day of talks.",
            "questions": json.dumps(questions),
            "confirmation_subject": "You're registered for {form_title}",
            "confirmation_body": (
                "Your session is {event_date} at {event_time} in {event_location}."
            ),
            "confirmation_format": "text",
            "event_start_at": "2027-04-15T09:00",
            "event_end_at": "2027-04-15T17:30",
            "event_timezone": "Asia/Kolkata",
            "event_location": "Bengaluru Tech Park",
            "event_url": "https://events.example.com/modern-web-summit",
            "publish": "yes",
        },
    )
    assert response.status_code == 302
    event_form = Form.query.filter_by(title="Modern Web Summit").one()

    public_view = client.get(f"/f/{event_form.public_id}")
    assert public_view.status_code == 200
    assert b"public-event-card" in public_view.data
    assert b"EVENT DETAILS" in public_view.data
    assert b"Bengaluru Tech Park" in public_view.data
    assert b"https://events.example.com/modern-web-summit" in public_view.data
    assert b"15 Apr 2027" in public_view.data
    assert b"09:00 AM" in public_view.data

    sent = []

    def fake_send(*args, **kwargs):
        sent.append((args, kwargs))
        return True

    monkeypatch.setattr("app.forms.routes.send_email", fake_send)
    response = client.post(
        f"/f/{event_form.public_id}",
        data={"email": "attendee@example.com"},
    )
    assert response.status_code == 200
    event_kwargs = sent[0][1]
    assert "15 April 2027" in sent[0][0][2]
    assert "Bengaluru Tech Park" in sent[0][0][2]
    assert event_kwargs["event_details"]["date"] == "Thursday, 15 April 2027"
    assert event_kwargs["event_details"]["location"] == "Bengaluru Tech Park"
    assert event_kwargs["event_url"] == "https://events.example.com/modern-web-summit"
    assert (
        event_kwargs["calendar_event"]["url"]
        == "https://events.example.com/modern-web-summit"
    )
    assert event_kwargs["calendar_event"]["timezone"] == "Asia/Kolkata"


def test_form_rejects_non_https_event_url(client):
    _register(client)
    response = client.post(
        "/forms/new",
        data={
            "title": "Unsafe event link",
            "questions": json.dumps(
                [{"id": "email", "title": "Email", "type": "email", "required": True}]
            ),
            "confirmation_subject": "Thanks",
            "confirmation_body": "Received",
            "event_url": "javascript:alert(1)",
            "publish": "no",
        },
    )

    assert response.status_code == 400
    assert b"valid HTTPS event URL" in response.data


def test_calendar_confirmation_has_styled_google_link_and_ics_attachment(app, monkeypatch):
    with app.app_context():
        organization = SimpleNamespace(
            name="Northstar Events",
            smtp_host="mail.example.com",
            smtp_port=587,
            smtp_username=None,
            smtp_password_encrypted=None,
            smtp_sender="no-reply@example.com",
            smtp_security="starttls",
        )
        calendar_event = {
            "uid": "event-7@forms.gamkers",
            "title": "Northstar Summit",
            "start": datetime(2027, 4, 15, 9),
            "end": datetime(2027, 4, 15, 10),
            "timezone": "Asia/Kolkata",
            "location": "Bengaluru",
            "description": "A summit",
            "url": "https://forms.example/f/abc123",
        }
        details = {
            "date": "Thursday, 15 April 2027",
            "start_time": "09:00 AM",
            "end_time": "10:00 AM",
            "timezone": "Asia/Kolkata",
            "timezone_label": "IST",
            "location": "Bengaluru",
        }
        message, _, _ = _styled_email(
            "attendee@example.com",
            "Registration confirmed",
            "You're registered.",
            organization,
            "text",
            event_details=details,
            event_title=calendar_event["title"],
            event_url=calendar_event["url"],
            calendar_url=_calendar_url(calendar_event),
        )
        html_part = message.get_body(preferencelist=("html",)).get_content()
        assert "Add to Google Calendar" in html_part
        assert "Bengaluru" in html_part
        assert "var(--" not in html_part

        invite = _calendar_invite(calendar_event)
        assert "DTSTART:20270415T033000Z" in invite
        assert "DTEND:20270415T043000Z" in invite
        assert "LOCATION:Bengaluru" in invite
        assert "forms.example/f/abc123" in invite

        sent_messages = []

        class FakeSMTP:
            def __init__(self, *args, **kwargs):
                pass

            def connect(self, host, port):
                return 220, b"ready"

            def ehlo(self):
                return 250, b"ready"

            def starttls(self, context=None):
                return 220, b"ready"

            def send_message(self, sent_message):
                sent_messages.append(sent_message)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        monkeypatch.setattr("app.mail.socket.getaddrinfo", lambda *a, **k: [
            (2, 1, 6, "", ("8.8.8.8", 587))
        ])
        monkeypatch.setattr("app.mail.smtplib.SMTP", FakeSMTP)
        assert send_email(
            "attendee@example.com",
            "Registration confirmed",
            "You're registered.",
            organization,
            event_details=details,
            event_title=calendar_event["title"],
            event_url=calendar_event["url"],
            calendar_event=calendar_event,
        )
        attachments = list(sent_messages[0].walk())
        calendar_attachment = next(
            part for part in attachments
            if part.get_content_type() == "text/calendar"
        )
        inline_logo = next(
            part for part in attachments if part.get_content_type() == "image/png"
        )
        assert "DTSTART:20270415T033000Z" in calendar_attachment.get_content()
        assert inline_logo["Content-ID"] == "<gamkers-portal-logo>"
        assert sent_messages[0].get_body(preferencelist=("plain",)) is None
        assert sent_messages[0].get_body(preferencelist=("html",)).get_content_type() == "text/html"


def test_certificate_full_content_saved_and_session_download(app, client, tmp_path, monkeypatch):
    app.config["CERTIFICATE_STORAGE_PATH"] = str(tmp_path / "private-certs")
    monkeypatch.setattr("app.certificates.services.send_email", lambda *a, **k: True)
    _register(client)
    user = User.query.first()
    batch = create_certificate_batch(
        organization_id=user.organization_id,
        creator_id=user.id,
        title="Cyber Security Masterclass",
        description="Awarded to {name} of {college_name} for outstanding performance on {date}.",
        records=[
            {
                "name": "Bob Cyber",
                "college_name": "MIT Cyber Lab",
                "email": "bob@example.com",
            }
        ],
    )
    cert = batch.certificates[0]
    assert cert.title == "Cyber Security Masterclass"
    assert cert.description == "Awarded to {name} of {college_name} for outstanding performance on {date}."
    assert "Bob Cyber" in cert.rendered_content
    assert "MIT Cyber Lab" in cert.rendered_content
    assert cert.issue_date is not None

    anon = client.application.test_client()
    assert anon.get(f"/certificates/verify/preview/{cert.certificate_number}").status_code == 403
    assert anon.get(f"/certificates/verify/download/{cert.certificate_number}").status_code == 403

    verify_res = anon.get(f"/certificates/verify?number={cert.certificate_number}")
    assert verify_res.status_code == 200
    assert b"AUTHENTIC CERTIFICATE" in verify_res.data
    assert b"Cyber Security Masterclass" in verify_res.data
    assert b"Bob Cyber" in verify_res.data
    assert b"MIT Cyber Lab" in verify_res.data
    assert cert.rendered_content.encode() in verify_res.data

    preview_res = anon.get(f"/certificates/verify/preview/{cert.certificate_number}")
    assert preview_res.status_code == 200
    assert preview_res.data.startswith(b"%PDF")
    download_res = anon.get(f"/certificates/verify/download/{cert.certificate_number}")
    assert download_res.status_code == 200
    assert download_res.data.startswith(b"%PDF")
    assert f"filename={cert.certificate_number}.pdf" in download_res.headers.get("Content-Disposition", "")


def test_public_form_event_card_rendering(client):
    _register(client)
    questions = [
        {"id": "email", "title": "Email", "type": "email", "required": True, "options": []},
        {"id": "name", "title": "Name", "type": "short_text", "required": True, "options": []},
    ]
    res = client.post(
        "/forms/new",
        data={
            "title": "Hackathon 2026",
            "description": "Annual hackathon event.",
            "questions": json.dumps(questions),
            "confirmation_subject": "Registered",
            "confirmation_body": "Confirmed",
            "event_start_at": "2026-12-01T10:00",
            "event_end_at": "2026-12-01T18:00",
            "event_timezone": "Asia/Kolkata",
            "event_location": "https://meet.google.com/abc-defg-hij",
            "event_url": "https://hackathon.example.com",
            "publish": "yes",
        },
        follow_redirects=True,
    )
    assert res.status_code == 200
    event_form = Form.query.filter_by(title="Hackathon 2026").first()
    anon = client.application.test_client()
    public_res = anon.get(f"/f/{event_form.public_id}")
    assert public_res.status_code == 200
    assert b"public-event-card" in public_res.data
    assert b"EVENT DETAILS" in public_res.data
    assert b"01 Dec 2026" in public_res.data
    assert b"10:00 AM" in public_res.data
    assert b"06:00 PM" in public_res.data
    assert b"Asia/Kolkata" in public_res.data
    assert b"https://meet.google.com/abc-defg-hij" in public_res.data
    assert b"https://hackathon.example.com" in public_res.data

    res_plain = client.post(
        "/forms/new",
        data={
            "title": "General Survey",
            "description": "Just a survey.",
            "questions": json.dumps([
                {"id": "email", "title": "Email", "type": "email", "required": True, "options": []},
                {"id": "feedback", "title": "Feedback", "type": "short_text", "required": True, "options": []},
            ]),
            "confirmation_subject": "Registered",
            "confirmation_body": "Confirmed",
            "publish": "yes",
        },
        follow_redirects=True,
    )
    assert res_plain.status_code == 200
    plain_form = Form.query.filter_by(title="General Survey").first()
    plain_res = anon.get(f"/f/{plain_form.public_id}")
    assert plain_res.status_code == 200
    assert b"public-event-card" not in plain_res.data
