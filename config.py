import os


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = os.getenv("DB_PORT", "3306")
    DB_NAME = os.getenv("DB_NAME")
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    SQLALCHEMY_DATABASE_URI = None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Valid for every SQLAlchemy pool class, including SQLite's StaticPool used by tests.
    # MySQL-only QueuePool sizing is added in app.create_app(); see there.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,  # Test each pooled connection before use (MySQL error 2006).
        "pool_recycle": 280,  # Replace connections before a 300 s server wait_timeout.
    }
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SECURE_COOKIES", "0") == "1"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
    SMTP_ENCRYPTION_KEY = os.getenv("SMTP_ENCRYPTION_KEY")
    APPLICATION_BASE_URL = os.getenv("APPLICATION_BASE_URL")
    CERTIFICATE_STORAGE_PATH = os.getenv("CERTIFICATE_STORAGE_PATH")
    CERTIFICATE_UPLOAD_PATH = os.getenv("CERTIFICATE_UPLOAD_PATH")
    CERTIFICATE_PDF_RETENTION_DAYS = int(os.getenv("CERTIFICATE_PDF_RETENTION_DAYS", "30"))
    CERTIFICATE_PDF_CLEANUP_INTERVAL_SECONDS = int(
        os.getenv("CERTIFICATE_PDF_CLEANUP_INTERVAL_SECONDS", "21600")
    )
    FORM_BANNER_STORAGE_PATH = os.getenv("FORM_BANNER_STORAGE_PATH")
    CERTIFICATE_MAX_RECORDS = int(os.getenv("CERTIFICATE_MAX_RECORDS", "500"))
    EMAIL_CSS_PATH = os.getenv("EMAIL_CSS_PATH")
    PORTAL_NAME = os.getenv("PORTAL_NAME", "GAMKERS PORTAL")
    BULK_MAIL_MAX_RECIPIENTS = int(os.getenv("BULK_MAIL_MAX_RECIPIENTS", "500"))
    DEFAULT_SMTP_HOST = os.getenv("SMTP_HOST")
    DEFAULT_SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    DEFAULT_SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    DEFAULT_SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    DEFAULT_SMTP_SENDER = os.getenv("SMTP_SENDER")
    DEFAULT_SMTP_SECURITY = os.getenv("SMTP_SECURITY", "starttls")
    INITIAL_ADMIN_NAME = os.getenv("INITIAL_ADMIN_NAME")
    INITIAL_ADMIN_EMAIL = os.getenv("INITIAL_ADMIN_EMAIL")
    INITIAL_ADMIN_PASSWORD = os.getenv("INITIAL_ADMIN_PASSWORD")
