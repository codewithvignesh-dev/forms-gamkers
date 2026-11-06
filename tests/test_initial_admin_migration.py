from pathlib import Path

import pytest
from flask_migrate import upgrade

from app import create_app
from app.extensions import db
from app.models import User


def migration_app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "migration-test-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'migration.db'}",
        }
    )


def test_fresh_migration_seeds_the_configured_single_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("INITIAL_ADMIN_NAME", "Portal Administrator")
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "a-unique-migration-password")
    app = migration_app(tmp_path)
    migration_directory = Path(app.root_path).parent / "migrations"

    with app.app_context():
        upgrade(directory=str(migration_directory))
        admin = db.session.scalar(db.select(User))

        assert admin.name == "Portal Administrator"
        assert admin.email == "admin@example.com"
        assert admin.role == "admin"
        assert admin.admin_slot == 1
        assert admin.organization.name == "GAMKERS PORTAL"
        assert admin.check_password("a-unique-migration-password")


def test_fresh_migration_requires_configured_admin_credentials(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv("INITIAL_ADMIN_NAME", raising=False)
    monkeypatch.delenv("INITIAL_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
    app = migration_app(tmp_path)
    migration_directory = Path(app.root_path).parent / "migrations"

    with app.app_context(), pytest.raises(SystemExit) as error:
        upgrade(directory=str(migration_directory))

    output = capsys.readouterr()
    assert error.value.code == 1
    assert "INITIAL_ADMIN_NAME" in output.err
    assert "INITIAL_ADMIN_PASSWORD" in output.err
