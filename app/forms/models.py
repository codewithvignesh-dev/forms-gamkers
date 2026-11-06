from app.extensions import db
from app.utils import utcnow


class Form(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False, index=True
    )
    creator_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    banner_image_path = db.Column(db.String(80))
    public_id = db.Column(db.String(32), nullable=False, unique=True, index=True)
    questions = db.Column(db.JSON, nullable=False, default=list)
    confirmation_subject = db.Column(
        db.String(200), nullable=False, default="Thanks for your response"
    )
    confirmation_body = db.Column(
        db.Text, nullable=False, default="Thanks for submitting this form."
    )
    confirmation_format = db.Column(
        db.String(12), nullable=False, default="text", server_default="text"
    )
    event_start_at = db.Column(db.DateTime)
    event_end_at = db.Column(db.DateTime)
    event_timezone = db.Column(
        db.String(64), nullable=False, default="Asia/Kolkata", server_default="Asia/Kolkata"
    )
    event_location = db.Column(db.String(255))
    event_url = db.Column(db.String(2048))
    is_published = db.Column(db.Boolean, nullable=False, default=False)
    closed_at = db.Column(db.DateTime)
    closed_message = db.Column(db.Text)
    parent_form_id = db.Column(
        db.Integer, db.ForeignKey("form.id", ondelete="CASCADE"), unique=True, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    organization = db.relationship("Organization", back_populates="forms")
    creator = db.relationship("User", back_populates="forms")
    submissions = db.relationship(
        "Submission", back_populates="form", cascade="all, delete-orphan"
    )


class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(
        db.Integer, db.ForeignKey("form.id", ondelete="CASCADE"), nullable=False, index=True
    )
    answers = db.Column(db.JSON, nullable=False)
    respondent_email = db.Column(db.String(255))
    email_status = db.Column(db.String(16), nullable=False, default="not_configured")
    submitted_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    form = db.relationship("Form", back_populates="submissions")
