from app.extensions import db
from app.utils import utcnow


class CertificateBatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False, index=True
    )
    creator_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    source_form_id = db.Column(
        db.Integer, db.ForeignKey("form.id", ondelete="SET NULL"), index=True
    )
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False)
    source_filename = db.Column(db.String(255))
    total_records = db.Column(db.Integer, nullable=False, default=0)
    generated_records = db.Column(db.Integer, nullable=False, default=0)
    failed_records = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(24), nullable=False, default="processing")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    completed_at = db.Column(db.DateTime)
    certificates = db.relationship(
        "CertificateRecord", back_populates="batch", cascade="all, delete-orphan"
    )


class CertificateRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(
        db.Integer,
        db.ForeignKey("certificate_batch.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    submission_id = db.Column(
        db.Integer, db.ForeignKey("submission.id", ondelete="SET NULL"), unique=True, index=True
    )
    recipient_name = db.Column(db.String(180), nullable=False)
    college_name = db.Column(db.String(180), nullable=False)
    recipient_email = db.Column(db.String(255))
    certificate_number = db.Column(db.String(48), nullable=False, unique=True)
    public_token = db.Column(db.String(64), nullable=False, unique=True)
    pdf_path = db.Column(db.String(512))
    pdf_expires_at = db.Column(db.DateTime, index=True)
    pdf_deleted_at = db.Column(db.DateTime)
    status = db.Column(db.String(16), nullable=False, default="pending")
    notification_status = db.Column(
        db.String(16), nullable=False, default="pending", server_default="skipped"
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    batch = db.relationship("CertificateBatch", back_populates="certificates")
    submission = db.relationship("Submission")


class CertificateUpload(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False, index=True
    )
    creator_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    stored_filename = db.Column(db.String(80), nullable=False, unique=True)
    original_filename = db.Column(db.String(255), nullable=False)
    total_records = db.Column(db.Integer, nullable=False)
    preview_records = db.Column(db.JSON, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
