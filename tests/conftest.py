import pytest

from app import create_app
from app.extensions import db


@pytest.fixture
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-key-not-for-production",
            "APPLICATION_BASE_URL": "https://forms.example",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "CERTIFICATE_STORAGE_PATH": str(tmp_path / "certificates"),
            "CERTIFICATE_UPLOAD_PATH": str(tmp_path / "certificate-uploads"),
            "FORM_BANNER_STORAGE_PATH": str(tmp_path / "form-banners"),
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
            "SMTP_ENCRYPTION_KEY": None,
            "DEFAULT_SMTP_HOST": None,
            "DEFAULT_SMTP_SENDER": None,
        }
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()
