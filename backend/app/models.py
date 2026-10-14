from datetime import UTC, date, datetime

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
