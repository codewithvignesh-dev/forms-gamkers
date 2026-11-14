"""Regression coverage for certificate access after removing session authorization."""

from app.certificates.services import create_certificate_batch
from app.models import User


def test_certificate_verification_does_not_use_session_scoped_access(
    app, client, tmp_path, monkeypatch
):
    app.config["CERTIFICATE_STORAGE_PATH"] = str(tmp_path / "private-certs")
    monkeypatch.setattr(
        "app.certificates.services.send_email", lambda *args, **kwargs: True
    )

    result = client.application.test_cli_runner().invoke(
        args=[
            "create-admin",
            "--name",
            "Issue 11 Test Admin",
            "--email",
            "issue11-admin@example.com",
        ],
        input="safe-test-password-2026\nsafe-test-password-2026\n",
    )
    assert result.exit_code == 0, result.output
    login = client.post(
        "/login",
        data={
            "email": "issue11-admin@example.com",
            "password": "safe-test-password-2026",
        },
        follow_redirects=True,
    )
    assert login.status_code == 200

    user = User.query.filter_by(email="issue11-admin@example.com").one()
    batch = create_certificate_batch(
        organization_id=user.organization_id,
        creator_id=user.id,
        title="Issue 11 Regression Certificate",
        description="Awarded to {name} of {college_name}.",
        records=[
            {
                "name": "Session Test Recipient",
                "college_name": "Test Institution",
                "email": "recipient@example.com",
            }
        ],
    )
    certificate = batch.certificates[0]

    # Public verification is explicitly authorized by the unguessable token,
    # not by a certificate-number entry stored in a browser session.
    visitor = app.test_client()
    verified = visitor.get(
        f"/certificates/verify?token={certificate.public_token}"
    )
    assert verified.status_code == 200
    assert b"AUTHENTIC CERTIFICATE" in verified.data
    assert certificate.recipient_name.encode() in verified.data

    with visitor.session_transaction() as session:
        assert "verified_certificates" not in session
        assert not any(key.startswith("verified_cert_") for key in session)

    # The old session-authorized endpoints must stay removed.
    assert (
        visitor.get(
            f"/certificates/verify/preview/{certificate.certificate_number}"
        ).status_code
        == 404
    )
    assert (
        visitor.get(
            f"/certificates/verify/download/{certificate.certificate_number}"
        ).status_code
        == 404
    )

    # A separate browser session can verify only when it has the public token.
    second_visitor = app.test_client()
    second_verified = second_visitor.get(
        f"/certificates/verify?token={certificate.public_token}"
    )
    assert second_verified.status_code == 200
    assert b"AUTHENTIC CERTIFICATE" in second_verified.data
