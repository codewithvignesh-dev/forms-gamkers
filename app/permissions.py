from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user


ROLE_LABELS = {
    "admin": "Admin",
    "developer": "Developer",
    "contributor": "Contributor",
}

ROLE_PERMISSIONS = (
    ("forms.manage", "Create, edit, publish, and close forms"),
    ("responses.view", "View form responses"),
    ("responses.export", "Export response data"),
    ("certificates.manage", "Generate and manage certificates"),
    ("bulk_mail.send", "Import contacts and send bulk mail"),
    ("settings.manage", "Change workspace and email settings"),
    ("team.manage", "Invite and manage team members"),
    ("permissions.manage", "Change role permissions"),
)

PROTECTED_PERMISSIONS = {"team.manage", "permissions.manage"}
PERMISSION_NAMES = {permission for permission, _label in ROLE_PERMISSIONS}

DEFAULT_ROLE_PERMISSIONS = {
    "admin": {permission for permission, _label in ROLE_PERMISSIONS},
    "developer": {
        "forms.manage",
        "responses.view",
        "responses.export",
        "certificates.manage",
    },
    "contributor": {"forms.manage", "responses.view"},
}


def requires_permission(permission):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.has_permission(permission):
                flash("You do not have permission to access that feature.", "error")
                return redirect(url_for("main.dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorator
