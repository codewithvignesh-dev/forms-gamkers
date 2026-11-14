# GAMKERS PORTAL

GAMKERS PORTAL is an internal workspace for the GAMKERS team. It provides
forms, response management, team access, bulk email, and certificate workflows
in one web application. Public account signup is disabled.

## Features

- Create, publish, and close forms; collect and export responses.
- Invite team members and assign admin, developer, or contributor roles.
- Change role permissions from the administrator settings.
- Import contacts from CSV and send individual bulk emails.
- Generate certificates, retain verification records, and verify certificates
  using the public verification page.
- Add banner images to forms and configure application email delivery.

The initial administrator is created during the first database migration using
the credentials configured in the environment. The portal allows one admin
account.

## Requirements

- Python 3.11 or newer
- MySQL or MariaDB for the application database
- Node.js and npm only if rebuilding the CSS
- SMTP settings if the application needs to send email

Use an InnoDB database with the `utf8mb4` character set.

## Installation

### 1. Get the project and create a Python environment

Run commands from the project root.

**Windows PowerShell**

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `.env` with your own settings; do not overwrite an existing configuration.

**macOS or Linux**

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
```

### 2. Configure environment variables

Edit `.env` and set your database details and initial administrator credentials.
The administrator password must be unique and at least 12 characters.

Generate secret values:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Use these values for `SECRET_KEY`, `SMTP_ENCRYPTION_KEY`, and
`INITIAL_ADMIN_PASSWORD`, respectively. Set `INITIAL_ADMIN_NAME` and
`INITIAL_ADMIN_EMAIL` to the intended administrator. Keep `.env` private.

| Variable | Description |
| --- | --- |
| `SECRET_KEY` | Flask session and security key |
| `DB_HOST`, `DB_PORT` | MySQL/MariaDB server connection |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Application database credentials |
| `APPLICATION_BASE_URL` | Public base URL used for generated links |
| `APP_ENV` | `production` (default) or `development`. Production sends session cookies over HTTPS only; set `development` in your local `.env` to sign in over plain `http://localhost` |
| `INITIAL_ADMIN_NAME` | Initial administrator's display name |
| `INITIAL_ADMIN_EMAIL` | Initial administrator's login email |
| `INITIAL_ADMIN_PASSWORD` | Initial administrator's password |
| `SMTP_ENCRYPTION_KEY` | Key used to protect saved SMTP passwords |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_SENDER` | Optional platform SMTP settings |
| `SMTP_SECURITY` | SMTP connection mode: `starttls` or `ssl` |
| `CERTIFICATE_STORAGE_PATH` | Certificate PDF directory; defaults to `var/certificates` |
| `CERTIFICATE_UPLOAD_PATH` | Temporary recipient upload directory |
| `FORM_BANNER_STORAGE_PATH` | Form banner image directory |

Optional paths default to private storage under `var/`. Relative paths are
resolved from the project root. Keep the database and `var/` data backed up.

### 3. Create the database and initialize the application

Create an empty MySQL/MariaDB database and user, then apply database migrations:

```bash
flask --app wsgi db upgrade
```

The migration creates the schema and initial administrator. It requires the
three `INITIAL_ADMIN_*` values on a fresh install. Existing installations with
an administrator keep their current account during upgrade.

Start the application locally:

```bash
flask --app wsgi run
```

Open `http://127.0.0.1:5000` in a browser. The application can also be started
through either WSGI entry point:

```bash
python wsgi.py
python passenger_wsgi.py
```

Do not use Flask's development server for production.

## Production installation

1. Configure Python 3.11+, MySQL/MariaDB, and persistent private storage.
2. Place the project outside the public web root where possible.
3. Install `requirements.txt` into the application's Python environment.
4. Configure the database, secret, HTTPS, initial admin, storage, and optional
   SMTP environment variables.
5. Run `flask --app wsgi db upgrade` from the project root using that Python
   environment.
6. Configure the host to serve `passenger_wsgi.py` with the WSGI application
   named `application`, or use the production WSGI server supported by the host.
7. Restart the application and verify that the admin can sign in.

For cPanel, create the Python application in **Setup Python App**, set
`passenger_wsgi.py` as the startup file, install the requirements into its
virtual environment, configure the environment variables, and run migrations
using SSH or the host's supported command-line facility. Database-only access
through phpMyAdmin is not sufficient to initialize the application.

## Email and certificates

Configure organisation email delivery from **Settings → Email delivery**.
Application emails are sent using the configured SMTP service.

Generated certificate PDFs are kept for 30 days by default; their verification
records remain in the database. Schedule PDF cleanup daily:

```bash
flask --app wsgi cleanup-expired-certificate-pdfs
```

Certificate PDFs, recipient-list uploads, and form banners are stored outside
the public static directory. For an existing installation moving from
`instance/`, back up and copy data before switching paths:

- `instance/certificates/` to `var/certificates/`
- `instance/certificates/uploads/` to `var/uploads/certificates/`
- `instance/form-banners/` to `var/uploads/form-banners/`

Keep the original data until certificates and banners are verified at their
new locations.
