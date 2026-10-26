from datetime import UTC, date, datetime
import secrets

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, func, false
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

from app.utc_datetime import UTCDateTime


class Base(DeclarativeBase):
    pass


class JobApplication(Base):
    __tablename__ = "job_applications"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    company_name: Mapped[str] = mapped_column(
        String(150), nullable=False
    )

    job_title: Mapped[str] = mapped_column(
        String(200), nullable=False
    )

    location: Mapped[str | None] = mapped_column(
        String(150), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="saved"
    )

    application_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )

    job_description: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now()
    )

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_job_applications_user_id_users", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    user: Mapped["User | None"] = relationship(back_populates="applications")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint("email = lower(trim(email)) AND length(email) BETWEEN 3 AND 320", name="ck_users_normalized_email"),
        CheckConstraint("substr(password_hash, 1, 10) = '$argon2id$' AND length(password_hash) <= 255", name="ck_users_argon2id_hash"),
        CheckConstraint("NOT is_active OR email_verified_at IS NOT NULL", name="ck_users_active_verified"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now(), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    professional_headline: Mapped[str | None] = mapped_column(String(160), nullable=True)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    portfolio_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    applications: Mapped[list[JobApplication]] = relationship(back_populates="user", passive_deletes="all")
    sessions: Mapped[list["AuthSession"]] = relationship(back_populates="user", passive_deletes="all")

    @validates("email")
    def normalize_email(self, key, value):
        # Syntax validation belongs to the future registration request schema.
        return value.strip().lower()


class AuthSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_token_hash", "token_hash", unique=True),
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_expires_at", "expires_at"),
        Index("ix_sessions_idle_expires_at", "idle_expires_at"),
        CheckConstraint("length(token_hash) = 32", name="ck_sessions_sha256_length"),
        CheckConstraint("expires_at > created_at", name="ck_sessions_absolute_expiration"),
        CheckConstraint("last_activity_at >= created_at AND last_activity_at < expires_at", name="ck_sessions_last_activity"),
        CheckConstraint("idle_expires_at > last_activity_at AND idle_expires_at <= expires_at", name="ck_sessions_idle_expiration"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Store hashlib.sha256(raw_token).digest(), never the raw bearer token.
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_sessions_user_id_users", ondelete="CASCADE"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=lambda: datetime.now(UTC), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    user: Mapped[User | None] = relationship(back_populates="sessions")

class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    __table_args__ = (
        Index(
            "ix_email_verification_tokens_token_hash",
            "token_hash",
            unique=True,
        ),
        CheckConstraint(
            "length(token_hash) = 32",
            name="ck_email_verification_tokens_sha256_length",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_email_verification_tokens_expiration",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[bytes] = mapped_column(
        LargeBinary(32),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
    )

    consumed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )


class EmailVerificationOutbox(Base):
    """Dormant delivery metadata; no bearer token, link, or email content."""

    __tablename__ = "email_verification_outbox"
    __table_args__ = (
        UniqueConstraint("token_id", name="uq_email_verification_outbox_token_id"),
        Index("ix_email_verification_outbox_due", "state", "next_attempt_at"),
        Index("ix_email_verification_outbox_lease", "state", "lease_expires_at"),
        CheckConstraint("length(nonce) = 32", name="ck_email_verification_outbox_nonce_length"),
        CheckConstraint("length(key_version) BETWEEN 1 AND 32", name="ck_email_verification_outbox_key_version"),
        CheckConstraint(
            "state IN ('queued', 'leased', 'delivered', 'cancelled', 'uncertain')",
            name="ck_email_verification_outbox_state",
        ),
        CheckConstraint("expires_at > created_at", name="ck_email_verification_outbox_expiration"),
        CheckConstraint(
            "next_attempt_at >= created_at AND next_attempt_at <= expires_at",
            name="ck_email_verification_outbox_schedule",
        ),
        CheckConstraint("attempts >= 0", name="ck_email_verification_outbox_attempts"),
        CheckConstraint("fencing_generation >= 0", name="ck_email_verification_outbox_generation"),
        CheckConstraint(
            "(state = 'leased' AND lease_owner IS NOT NULL AND length(lease_owner) = 16 "
            "AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'leased' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_email_verification_outbox_lease_state",
        ),
        CheckConstraint(
            "failure_category IS NULL OR failure_category IN "
            "('transport', 'timeout', 'storage', 'configuration', 'uncertain', 'other')",
            name="ck_email_verification_outbox_failure_category",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_id: Mapped[int] = mapped_column(
        ForeignKey("email_verification_tokens.id", name="fk_email_verification_outbox_token_id", ondelete="CASCADE"),
        nullable=False,
    )
    nonce: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, default=lambda: secrets.token_bytes(32))
    key_version: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", server_default="queued")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    lease_owner: Mapped[bytes | None] = mapped_column(LargeBinary(16), nullable=True)
    fencing_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failure_category: Mapped[str | None] = mapped_column(String(32), nullable=True)

    def __repr__(self):
        return "<EmailVerificationOutbox>"
