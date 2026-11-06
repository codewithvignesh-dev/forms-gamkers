import io
import json
import socket
from datetime import timedelta
from types import SimpleNamespace

from app.extensions import db
from app.models import Form, Organization, PasswordReset, RolePermission, User, utcnow
from app.security import hash_token


def register(client, email="admin@example.com"):
    result = client.application.test_cli_runner().invoke(
        args=["create-admin", "--name", "Alex Admin", "--email", email],
        input="a-very-long-password\na-very-long-password\n",
    )
    assert result.exit_code == 0, result.output
    return login(client, email)


def login(client, email="admin@example.com"):
    return client.post(
        "/login",
        data={"email": email, "password": "a-very-long-password"},
        follow_redirects=True,
    )


def test_public_signup_is_disabled_and_cli_creates_the_single_admin(client, app):
    assert client.get("/signup").status_code == 404
    assert client.post("/signup").status_code == 404
    response = register(client)
    assert response.status_code == 200
    assert b"GAMKERS PORTAL" in response.data
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "admin@example.com"))
        assert user.role == "admin"
        assert user.admin_slot == 1
        assert user.organization.name == "GAMKERS PORTAL"
    result = client.application.test_cli_runner().invoke(
        args=["create-admin", "--name", "Another Admin", "--email", "other@example.com"],
        input="another-safe-password\nanother-safe-password\n",
    )
    assert result.exit_code != 0
    assert "single admin account" in result.output


def test_form_can_be_published_and_sends_a_confirmation(app, client, monkeypatch):
    register(client)
    questions = [
        {"id": "name", "title": "Your name", "type": "short_text", "required": True, "options": []},
        {"id": "email", "title": "Your email", "type": "email", "required": True, "options": []},
    ]
    response = client.post(
        "/forms/new",
        data={
            "title": "Workshop registration",
            "description": "Reserve a place.",
            "questions": json.dumps(questions),
            "confirmation_subject": "You're on the list, {name}",
            "confirmation_body": "Thanks {name}. We will write to {email}.",
            "publish": "yes",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        form = db.session.scalar(db.select(Form).where(Form.title == "Workshop registration"))
        assert form.is_published
        public_id = form.public_id

    sent = []
    monkeypatch.setattr("app.forms.routes.send_email", lambda *args: sent.append(args) or True)
    response = client.post(
        f"/f/{public_id}",
        data={"name": "Alex", "email": "alex@example.com"},
    )
    assert response.status_code == 200
    assert b"confirmation email is on its way" in response.data
    assert sent[0][0:3] == (
        "alex@example.com",
        "You're on the list, Alex",
        "Thanks Alex. We will write to alex@example.com.",
    )


def test_published_form_requires_respondent_email(client):
    register(client)
    response = client.post(
        "/forms/new",
        data={
            "title": "No email",
            "questions": json.dumps(
                [{"id": "name", "title": "Name", "type": "short_text", "required": True}]
            ),
            "confirmation_subject": "Thanks",
            "confirmation_body": "Received",
            "publish": "yes",
        },
    )
    assert response.status_code == 400
    assert b"Add a required email question" in response.data


def test_tenant_form_access_is_scoped_to_the_current_organisation(app, client):
    register(client)
    with app.app_context():
        other_org = Organization(name="Other company")
        other_admin = User(name="Other", email="other@example.com", role="developer", organization=other_org)
        other_admin.set_password("another-long-password")
        other_form = Form(
            organization=other_org,
            creator=other_admin,
            title="Private form",
            public_id="private-form",
            questions=[],
        )
        db.session.add_all([other_admin, other_form])
        db.session.commit()
        form_id = other_form.id
    assert client.get(f"/forms/{form_id}/edit").status_code == 404


def test_member_cannot_change_organisation_settings(app, client):
    register(client)
    with app.app_context():
        member = User(
            organization_id=1,
            name="Sam Member",
            email="member@example.com",
            role="contributor",
        )
        member.set_password("a-very-long-password")
        db.session.add(member)
        db.session.commit()
    client.post("/logout")
    login(client, "member@example.com")
    response = client.post(
        "/settings/organisation",
        data={"name": "Changed", "website": "https://example.com"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"do not have permission" in response.data
    with app.app_context():
        assert db.session.get(Organization, 1).name == "GAMKERS PORTAL"


def test_password_reset_token_is_single_use(app, client):
    with app.app_context():
        organization = Organization(name="Northstar Labs")
        user = User(name="Alex", email="alex@example.com", role="admin", organization=organization)
        user.set_password("old-password-that-is-long")
        db.session.add(user)
        db.session.flush()
        token = "one-time-test-token"
        reset = PasswordReset(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=utcnow() + timedelta(minutes=30),
        )
        db.session.add(reset)
        db.session.commit()
    response = client.post(
        f"/reset-password/{token}",
        data={"password": "new-password-that-is-long"},
        follow_redirects=True,
    )
    assert b"Your password has been updated" in response.data
    response = client.get(f"/reset-password/{token}", follow_redirects=True)
    assert b"invalid or has expired" in response.data
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "alex@example.com"))
        assert user.check_password("new-password-that-is-long")


def test_password_reset_link_uses_configured_origin_not_host_header(app, client, monkeypatch):
    with app.app_context():
        organization = Organization(name="Northstar Labs")
        user = User(name="Alex", email="alex@example.com", role="admin", organization=organization)
        user.set_password("old-password-that-is-long")
        db.session.add(user)
        db.session.commit()

    sent = []
    monkeypatch.setattr("app.auth.routes.send_email", lambda *args: sent.append(args) or True)
    client.post(
        "/forgot-password",
        data={"email": "alex@example.com"},
        headers={"Host": "attacker.example"},
    )
    assert sent[0][2].startswith("Reset your password")
    assert "https://forms.example/reset-password/" in sent[0][2]
    assert "attacker.example" not in sent[0][2]


def test_smtp_delivery_rejects_private_network_targets(app, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(
            "app.mail.socket.getaddrinfo",
            lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 587))
            ],
        )
        organization = SimpleNamespace(
            smtp_host="mail.example.com",
            smtp_port=587,
            smtp_username=None,
            smtp_password_encrypted=None,
            smtp_sender="noreply@example.com",
            smtp_security="starttls",
        )
        from app.mail import send_email

        try:
            send_email("user@example.com", "Subject", "Body", organization)
        except ValueError as error:
            assert "public IP addresses" in str(error)
        else:
            raise AssertionError("Private SMTP targets must be rejected.")


def test_bulk_mail_import_sends_individual_personalised_messages(client, monkeypatch):
    register(client)
    sent = []
    monkeypatch.setattr(
        "app.bulk_mail.send_email",
        lambda *args: sent.append(args) or True,
    )
    response = client.post(
        "/mail/bulk",
        data={
            "subject": "Hello {name}",
            "body": "Hello {name} ({email})",
            "recipient_file": (
                io.BytesIO(
                    b"email,name\nalex@example.com,Alex\n"
                    b"alex@example.com,Duplicate\ninvalid,Nope\nsam@example.com,Sam\n"
                ),
                "team.csv",
            ),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"2 sent, 0 failed, 1 invalid and 1 duplicate rows skipped" in response.data
    assert [(message[0], message[1], message[2]) for message in sent] == [
        ("alex@example.com", "Hello Alex", "Hello Alex (alex@example.com)"),
        ("sam@example.com", "Hello Sam", "Hello Sam (sam@example.com)"),
    ]


def test_admin_can_change_role_permissions_without_granting_admin_privilege(
    client, app
):
    register(client)
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@example.com"))
        user = User(
            name="Sam",
            email="sam@example.com",
            role="contributor",
            organization_id=admin.organization_id,
        )
        user.set_password("a-very-long-password")
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    response = client.post(
        "/team/permissions",
        data={
            "developer:bulk_mail.send": "on",
            "admin:team.manage": "off",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.email == "admin@example.com"))
        assert admin.has_permission("team.manage")
        permission = RolePermission.query.filter_by(
            organization_id=admin.organization_id,
            role="developer",
            permission="bulk_mail.send",
        ).one()
        assert permission.enabled
        developer = User(
            name="Dev",
            email="dev@example.com",
            role="developer",
            organization_id=admin.organization_id,
        )
        developer.set_password("a-very-long-password")
        db.session.add(developer)
        db.session.flush()
        assert developer.has_permission("bulk_mail.send")
        assert not db.session.get(User, user_id).has_permission("bulk_mail.send")

    response = client.post(
        f"/team/{user_id}/role",
        data={"role": "admin"},
        follow_redirects=True,
    )
    assert b"Choose the developer or contributor role" in response.data
    with app.app_context():
        assert db.session.get(User, user_id).role == "contributor"
