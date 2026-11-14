import secrets
import threading
import time
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, g, render_template
from sqlalchemy.engine import URL

load_dotenv()

from config import get_config
from app.extensions import csrf, db, limiter, login_manager, migrate


def _warn_about_weak_production_settings(app):
    base_url = app.config.get("APPLICATION_BASE_URL")
    if base_url and not base_url.lower().startswith("https://"):
        app.logger.warning("APPLICATION_BASE_URL should start with https:// in production.")
    if str(app.config.get("RATELIMIT_STORAGE_URI", "")).startswith("memory://"):
        app.logger.warning(
            "RATELIMIT_STORAGE_URI is in-memory: each worker keeps its own limits. "
            "Use a shared backend such as redis:// in production."
        )


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(get_config())
    if test_config:
        app.config.update(test_config)
    if app.config["APP_ENV"] == "production" and not app.config.get("TESTING"):
        _warn_about_weak_production_settings(app)
    if not app.config.get("SECRET_KEY") and not app.config.get("TESTING"):
        raise RuntimeError("Set SECRET_KEY before starting the application.")
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        database_settings = {
            "DB_HOST": app.config.get("DB_HOST"),
            "DB_PORT": app.config.get("DB_PORT"),
            "DB_NAME": app.config.get("DB_NAME"),
            "DB_USER": app.config.get("DB_USER"),
            "DB_PASSWORD": app.config.get("DB_PASSWORD"),
        }
        missing_settings = [
            name for name, value in database_settings.items() if value in (None, "")
        ]
        if missing_settings:
            raise RuntimeError(
                "Set the MySQL settings in .env: " + ", ".join(missing_settings) + "."
            )
        try:
            database_port = int(database_settings["DB_PORT"])
        except (TypeError, ValueError) as error:
            raise RuntimeError("DB_PORT must be a valid TCP port number.") from error
        if not 1 <= database_port <= 65535:
            raise RuntimeError("DB_PORT must be between 1 and 65535.")
        app.config["SQLALCHEMY_DATABASE_URI"] = URL.create(
            drivername="mysql+pymysql",
            username=database_settings["DB_USER"],
            password=database_settings["DB_PASSWORD"],
            host=database_settings["DB_HOST"],
            port=database_port,
            database=database_settings["DB_NAME"],
            query={"charset": "utf8mb4"},
        )
    project_root = Path(app.root_path).parent
    storage_paths = {
        "CERTIFICATE_STORAGE_PATH": "var/certificates",
        "CERTIFICATE_UPLOAD_PATH": "var/uploads/certificates",
        "FORM_BANNER_STORAGE_PATH": "var/uploads/form-banners",
    }
    for setting, default in storage_paths.items():
        path = Path(app.config.get(setting) or default)
        if not path.is_absolute():
            path = project_root / path
        app.config[setting] = str(path.resolve())
    if not app.config.get("EMAIL_CSS_PATH"):
        app.config["EMAIL_CSS_PATH"] = str(
            Path(app.static_folder) / "css" / "app.css"
        )

    # Must run before db.init_app(): Flask-SQLAlchemy builds the engine there.
    # QueuePool-only options crash SQLite's StaticPool, so add them for MySQL only.
    database_uri = app.config["SQLALCHEMY_DATABASE_URI"]
    database_driver = getattr(
        database_uri, "drivername", str(database_uri).split(":", 1)[0]
    )
    if database_driver.startswith("mysql"):
        engine_options = dict(app.config.get("SQLALCHEMY_ENGINE_OPTIONS") or {})
        engine_options.setdefault("pool_size", 5)
        engine_options.setdefault("max_overflow", 5)
        engine_options.setdefault("pool_timeout", 20)
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = engine_options

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    from app import models
    from app.auth.routes import auth
    from app.forms.routes import forms
    from app.main.routes import main
    from app.organization.routes import organization
    from app.certificates.routes import certificates
    from app.bulk_mail import bulk_mail

    app.register_blueprint(main)
    app.register_blueprint(auth)
    app.register_blueprint(forms)
    app.register_blueprint(organization)
    app.register_blueprint(certificates)
    app.register_blueprint(bulk_mail)

    @app.before_request
    def create_csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(16)

    cleanup_state = {"last_run": 0.0}
    cleanup_lock = threading.Lock()

    @app.before_request
    def cleanup_expired_certificate_pdfs():
        now = time.monotonic()
        if now - cleanup_state["last_run"] < app.config[
            "CERTIFICATE_PDF_CLEANUP_INTERVAL_SECONDS"
        ]:
            return
        if not cleanup_lock.acquire(blocking=False):
            return
        try:
            if now - cleanup_state["last_run"] < app.config[
                "CERTIFICATE_PDF_CLEANUP_INTERVAL_SECONDS"
            ]:
                return
            cleanup_state["last_run"] = now
            from app.certificates.services import cleanup_expired_certificate_pdfs

            cleanup_expired_certificate_pdfs()
        except Exception:
            db.session.rollback()
            app.logger.exception("Scheduled certificate PDF cleanup failed.")
        finally:
            cleanup_lock.release()

    @app.cli.command("cleanup-expired-certificate-pdfs")
    def cleanup_certificate_pdfs_command():
        """Delete expired certificate PDFs while retaining their verification records."""
        from app.certificates.services import cleanup_expired_certificate_pdfs

        removed = 0
        while True:
            batch_removed = cleanup_expired_certificate_pdfs()
            removed += batch_removed
            if batch_removed < 500:
                break
        click.echo(f"Deleted {removed} expired certificate PDF(s).")

    @app.cli.command("create-admin")
    @click.option("--name", prompt="Admin name")
    @click.option("--email", prompt="Admin email")
    @click.password_option(prompt="Admin password", confirmation_prompt=True)
    def create_admin(name, email, password):
        """Create the one administrator account for this portal."""
        from app.auth.models import User
        from app.organization.models import Organization
        from app.security import valid_email

        email = email.strip().lower()
        if not name.strip() or len(name.strip()) > 120:
            raise click.ClickException("Enter a name of at most 120 characters.")
        if not valid_email(email):
            raise click.ClickException("Enter a valid email address.")
        if not 12 <= len(password) <= 1024:
            raise click.ClickException("Use a password with 12–1,024 characters.")
        if db.session.scalar(db.select(User.id).where(User.admin_slot == 1)):
            raise click.ClickException("The portal already has its single admin account.")
        if db.session.scalar(db.select(User.id).where(User.email == email)):
            raise click.ClickException("An account already exists for that email address.")

        organization = Organization.query.order_by(Organization.id).first()
        if organization is None:
            organization = Organization(name=app.config["PORTAL_NAME"])
        else:
            organization.name = app.config["PORTAL_NAME"]
        user = User(
            name=name.strip(),
            email=email,
            role="admin",
            admin_slot=1,
            organization=organization,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Admin account created for {email}.")

    @app.after_request
    def set_security_headers(response):
        nonce = getattr(g, "csp_nonce", None)
        style_src = f"'self' 'nonce-{nonce}'" if nonce else "'self'"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            f"default-src 'self'; style-src {style_src}; script-src 'self'; "
            "img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; object-src 'none'",
        )
        return response

    @app.context_processor
    def inject_template_globals():
        from datetime import datetime, timezone

        return {
            "current_year": datetime.now(timezone.utc).year,
            "csp_nonce": getattr(g, "csp_nonce", ""),
        }

    @app.errorhandler(413)
    def request_too_large(_error):
        return render_template("errors/413.html"), 413

    @app.errorhandler(410)
    def certificate_pdf_expired(_error):
        return render_template("errors/410.html"), 410

    return app
