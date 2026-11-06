"""
Comprehensive Test Suite for Forms Gamkers.
Validates the full application lifecycle, security, RBAC, form builder, and CSV export.
"""
import codecs
import csv
import io
import json
import os
import re
import sys
import unittest
from unittest.mock import patch

from sqlalchemy.engine import URL

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.extensions import db
from app.models import Form, Invitation, Organization, PasswordReset, Submission, User
from app.security import decrypt_smtp_password, hash_token, new_token


class FormsGamkersTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_database_uri = "sqlite:///:memory:"
        if os.getenv("TEST_DB_HOST"):
            test_database_settings = {
                "TEST_DB_HOST": os.getenv("TEST_DB_HOST"),
                "TEST_DB_PORT": os.getenv("TEST_DB_PORT", "3306"),
                "TEST_DB_NAME": os.getenv("TEST_DB_NAME"),
                "TEST_DB_USER": os.getenv("TEST_DB_USER"),
                "TEST_DB_PASSWORD": os.getenv("TEST_DB_PASSWORD"),
            }
            missing_settings = [
                name for name, value in test_database_settings.items() if not value
            ]
            if missing_settings:
                raise RuntimeError(
                    "Set all MySQL test settings: " + ", ".join(missing_settings)
                )
            test_database_uri = URL.create(
                drivername="mysql+pymysql",
                username=test_database_settings["TEST_DB_USER"],
                password=test_database_settings["TEST_DB_PASSWORD"],
                host=test_database_settings["TEST_DB_HOST"],
                port=int(test_database_settings["TEST_DB_PORT"]),
                database=test_database_settings["TEST_DB_NAME"],
                query={"charset": "utf8mb4"},
            )
        cls.test_config = {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": test_database_uri,
            "SECRET_KEY": "gamkers-test-secret-key-32-bytes-long",
            "APPLICATION_BASE_URL": "http://localhost:5000",
            "SMTP_ENCRYPTION_KEY": "rZ1Hn1Z5Q_4C2v1h5T-cK9qN7fE3jP0wX6sB4vM8lD8=",
            "SERVER_NAME": "localhost:5000",
        }
        cls.app = create_app(test_config=cls.test_config)
        cls.client = cls.app.test_client()

    def setUp(self):
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_01_landing_page_and_branding(self):
        """Landing page renders with Gamkers logo, cyber aesthetic and security headers."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"GAMKERS", res.data)
        self.assertIn(b"FORMS", res.data)
        self.assertIn(b"logo.png", res.data)
        nonce = re.search(rb'<style nonce="([^"]+)"', res.data).group(1).decode()
        self.assertIn(f"'nonce-{nonce}'", res.headers["Content-Security-Policy"])
        for asset in (
            "img/gamkers-first-form-guide.gif",
            "img/learning-plan-form.svg",
            "img/learning-question-types.svg",
            "img/learning-publish-review.svg",
        ):
            self.assertEqual(self.client.get(f"/static/{asset}").status_code, 200)
        self.assertEqual(res.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(res.headers.get("X-Frame-Options"), "DENY")
        self.assertIn("Content-Security-Policy", res.headers)

    def test_02_signup_and_workspace_creation(self):
        """Public signup stays disabled; the CLI provisions the single admin."""
        self.assertEqual(self.client.get("/signup").status_code, 404)
        admin = User.query.filter_by(email="akash@gamkers.in").first()
        if admin is None:
            result = self.app.test_cli_runner().invoke(
                args=[
                    "create-admin",
                    "--name",
                    "Akash M",
                    "--email",
                    "akash@gamkers.in",
                ],
                input="MasterCyberPassword2026!\nMasterCyberPassword2026!\n",
            )
            self.assertEqual(result.exit_code, 0, result.output)
        admin = User.query.filter_by(email="akash@gamkers.in").first()
        self.assertIsNotNone(admin)
        self.assertTrue(admin.is_org_admin)
        self.assertEqual(admin.organization.name, "GAMKERS PORTAL")
        self.client.post(
            "/login",
            data={
                "email": "akash@gamkers.in",
                "password": "MasterCyberPassword2026!",
            },
            follow_redirects=True,
        )

    def test_03_login_logout_flow(self):
        """Login and logout authentication flow."""
        self.test_02_signup_and_workspace_creation()
        self.client.post("/logout", follow_redirects=True)

        # Invalid password
        res = self.client.post("/login", data={"email": "akash@gamkers.in", "password": "WrongPassword123!"}, follow_redirects=True)
        self.assertIn(b"Email or password is incorrect", res.data)

        # Valid password
        res = self.client.post("/login", data={"email": "akash@gamkers.in", "password": "MasterCyberPassword2026!"}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Workspace", res.data)

    def test_04_smtp_settings_encryption(self):
        """Organisation SMTP settings saved with AES-Fernet encryption."""
        self.test_02_signup_and_workspace_creation()
        smtp_payload = {
            "host": "mail.gamkers.in",
            "port": "587",
            "sender": "notifications@gamkers.in",
            "username": "smtp@gamkers.in",
            "password": "UltraSecretSMTPKey999!",
            "security": "starttls",
        }
        res = self.client.post("/settings/email", data=smtp_payload, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        org = Organization.query.first()
        self.assertEqual(org.smtp_host, "mail.gamkers.in")
        self.assertEqual(org.smtp_port, 587)
        self.assertEqual(decrypt_smtp_password(org.smtp_password_encrypted), "UltraSecretSMTPKey999!")

    def test_05_team_invitations_and_roles(self):
        """Admin invites a developer, and non-admin access is enforced."""
        self.test_02_signup_and_workspace_creation()

        with patch("app.organization.routes.send_email", return_value=True):
            res = self.client.post("/team", data={"email": "teammate@gamkers.in", "role": "developer"}, follow_redirects=True)
            self.assertEqual(res.status_code, 200)

        invite = Invitation.query.filter_by(email="teammate@gamkers.in").first()
        self.assertIsNotNone(invite)

        # Accept invitation
        token = "test_invite_token_1234567890"
        invite.token_hash = hash_token(token)
        db.session.commit()

        anon = self.app.test_client()
        res = anon.post(f"/invite/{token}", data={
            "name": "Teammate User",
            "password": "TeammatePassword2026!",
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        member = User.query.filter_by(email="teammate@gamkers.in").first()
        self.assertIsNotNone(member)
        self.assertEqual(member.role, "developer")
        self.assertFalse(member.is_org_admin)

        # Team permissions are required for portal settings.
        member_client = self.app.test_client()
        member_client.post("/login", data={"email": "teammate@gamkers.in", "password": "TeammatePassword2026!"}, follow_redirects=True)
        res = member_client.get("/settings/email")
        self.assertEqual(res.status_code, 302)  # Redirects with permission error

    def _create_test_form(self):
        self.test_02_signup_and_workspace_creation()
        valid_questions = json.dumps([
            {"id": "email", "title": "Your Email", "type": "email", "required": True, "options": []},
            {"id": "name", "title": "Your Name", "type": "short_text", "required": True, "options": []},
            {"id": "category", "title": "Focus Area", "type": "choice", "required": True, "options": ["Bug Bounty", "Red Teaming"]},
        ])
        res = self.client.post("/forms/new", data={
            "title": "Gamkers CTF 2026 Registration",
            "description": "Register to receive challenges.",
            "confirmation_subject": "CTF Registration: {email}",
            "confirmation_body": "Hi {name},\n\nYou are registered for track {category}!",
            "publish": "yes",
            "questions": valid_questions,
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        return Form.query.filter_by(title="Gamkers CTF 2026 Registration").first()

    def test_06_form_builder_and_validation(self):
        """Create, publish, and validate a form."""
        self.test_02_signup_and_workspace_creation()

        # Reject publishing if no required email question
        invalid_questions = json.dumps([{"id": "name", "title": "Full Name", "type": "short_text", "required": True, "options": []}])
        res = self.client.post("/forms/new", data={
            "title": "Invalid Form",
            "description": "Missing email",
            "confirmation_subject": "Thanks",
            "confirmation_body": "Thanks",
            "publish": "yes",
            "questions": invalid_questions,
        }, follow_redirects=True)
        self.assertIn(b"Add a required email question", res.data)

        # Valid form with required email question
        form = self._create_test_form()
        self.assertIsNotNone(form)
        self.assertTrue(form.is_published)

    def test_07_public_form_view_and_submission(self):
        """Respondent views and submits form; instant confirmation email is dispatched."""
        form = self._create_test_form()
        anon = self.app.test_client()

        # Public view
        res = anon.get(f"/f/{form.public_id}")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Gamkers CTF 2026 Registration", res.data)
        self.assertIn(b"Focus Area", res.data)

        # Public submit with mock email dispatch
        with patch("app.forms.routes.send_email", return_value=True) as mock_send:
            res = anon.post(f"/f/{form.public_id}", data={
                "email": "hacker@cybersec.org",
                "name": "Alex Hacker",
                "category": "Bug Bounty",
            }, follow_redirects=True)
            self.assertEqual(res.status_code, 200)
            self.assertIn(b"submitted successfully", res.data)
            self.assertTrue(mock_send.called)
            args, _ = mock_send.call_args
            recipient, subject, body, org = args
            self.assertEqual(recipient, "hacker@cybersec.org")
            self.assertEqual(subject, "CTF Registration: hacker@cybersec.org")
            self.assertIn("Hi Alex Hacker,", body)
            self.assertIn("Bug Bounty", body)

        sub = Submission.query.filter_by(respondent_email="hacker@cybersec.org").first()
        self.assertIsNotNone(sub)
        self.assertEqual(sub.email_status, "sent")

    def test_08_csv_export_feature(self):
        """Admins can view and export form responses as CSV."""
        form = self._create_test_form()
        anon = self.app.test_client()
        with patch("app.forms.routes.send_email", return_value=True):
            anon.post(f"/f/{form.public_id}", data={
                "email": "respondent1@gamkers.in",
                "name": "First Respondent",
                "category": "Red Teaming",
            }, follow_redirects=True)

        # Responses view
        res = self.client.get(f"/forms/{form.id}/responses")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"respondent1@gamkers.in", res.data)
        self.assertIn(b"Export CSV", res.data)

        # CSV export endpoint
        res = self.client.get(f"/forms/{form.id}/export.csv")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.headers.get("Content-Type").startswith("text/csv"))
        self.assertIn(b"Submission ID", res.data)
        self.assertIn(b"respondent1@gamkers.in", res.data)
        self.assertIn(b"First Respondent", res.data)
        self.assertIn(b"Red Teaming", res.data)

    def test_09_password_reset_flow(self):
        """Single-use password reset link flow."""
        self.test_02_signup_and_workspace_creation()
        admin = User.query.filter_by(email="akash@gamkers.in").first()

        # Request reset
        with patch("app.auth.routes.send_email", return_value=True):
            res = self.client.post("/forgot-password", data={"email": "akash@gamkers.in"}, follow_redirects=True)
            self.assertEqual(res.status_code, 200)

        reset = PasswordReset.query.filter_by(user_id=admin.id, used_at=None).first()
        self.assertIsNotNone(reset)

        token = "test_reset_token_secret_12345"
        reset.token_hash = hash_token(token)
        db.session.commit()

        # Complete reset
        anon = self.app.test_client()
        res = anon.post(f"/reset-password/{token}", data={
            "password": "NewSecurePassword2026!",
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        # Reset token is marked used
        db.session.refresh(reset)
        self.assertIsNotNone(reset.used_at)

        # Login with new password
        res = anon.post("/login", data={
            "email": "akash@gamkers.in",
            "password": "NewSecurePassword2026!",
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Workspace", res.data)

    def test_10_csv_export_neutralises_formula_injection(self):
        """CSV export prefixes formula-like cells with ' (CWE-1236) and sets safe headers."""
        self.test_02_signup_and_workspace_creation()
        questions = json.dumps([
            {"id": "email", "title": "Your Email", "type": "email", "required": True, "options": []},
            {"id": "payload", "title": "=HYPERLINK(\"http://evil.test\")", "type": "short_text", "required": True, "options": []},
            {"id": "tags", "title": "Tags", "type": "checkbox", "required": False, "options": ["-risky", "safe"]},
        ])
        res = self.client.post("/forms/new", data={
            "title": "தமிழ் Injection Test",
            "description": "CSV injection regression test.",
            "confirmation_subject": "Thanks {email}",
            "confirmation_body": "Thanks",
            "publish": "yes",
            "questions": questions,
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        form = Form.query.filter_by(title="தமிழ் Injection Test").first()
        self.assertIsNotNone(form)

        payloads = ["=cmd|'/C calc'!A0", "@SUM(1+1)", "+12345-1", "-2+3"]
        anon = self.app.test_client()
        with patch("app.forms.routes.send_email", return_value=True):
            for index, payload in enumerate(payloads):
                res = anon.post(f"/f/{form.public_id}", data={
                    "email": f"respondent{index}@gamkers.in",
                    "payload": payload,
                    "tags": ["-risky", "safe"],
                })
                self.assertEqual(res.status_code, 200)
            res = anon.post(f"/f/{form.public_id}", data={
                "email": "=evil@example.com",
                "payload": "Plain answer",
                "tags": ["safe", "-risky"],
            })
            self.assertEqual(res.status_code, 200)

        res = self.client.get(f"/forms/{form.id}/export.csv")
        body = res.data  # Always consume streamed bodies so the request context closes.
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(res.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(res.headers["Cache-Control"], "no-store")
        disposition = res.headers["Content-Disposition"]
        self.assertTrue(disposition.isascii())
        self.assertEqual(disposition, f'attachment; filename="responses_{form.id}_Injection_Test.csv"')
        self.assertTrue(body.startswith(codecs.BOM_UTF8))

        rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
        self.assertEqual(
            rows[0],
            ["Submission ID", "Submitted At (UTC)", "Respondent Email", "Your Email", "'=HYPERLINK(\"http://evil.test\")", "Tags"],
        )
        data_rows = rows[1:]
        self.assertEqual(len(data_rows), len(payloads) + 1)
        payload_cells = {row[4] for row in data_rows}
        for payload in payloads:
            self.assertIn("'" + payload, payload_cells)
            self.assertNotIn(payload, payload_cells)
        by_email = {row[2]: row for row in data_rows}
        self.assertIn("'=evil@example.com", by_email)
        self.assertEqual(by_email["'=evil@example.com"][3], "'=evil@example.com")
        self.assertEqual(by_email["'=evil@example.com"][5], "safe, -risky")
        self.assertEqual(by_email["respondent0@gamkers.in"][5], "'-risky, safe")


if __name__ == "__main__":
    unittest.main()
