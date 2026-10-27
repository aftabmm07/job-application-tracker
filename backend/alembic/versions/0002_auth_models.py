"""Add users, sessions, and nullable ownership; never assign legacy records."""
from alembic import op
import sqlalchemy as sa

revision = "0002_auth_models"
down_revision = "0001_legacy"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("email = lower(trim(email)) AND length(email) BETWEEN 3 AND 320", name="ck_users_normalized_email"),
        sa.CheckConstraint("substr(password_hash, 1, 10) = '$argon2id$' AND length(password_hash) <= 255", name="ck_users_argon2id_hash"),
        sa.CheckConstraint("NOT is_active OR email_verified_at IS NOT NULL", name="ck_users_active_verified"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_sessions_user_id_users", ondelete="CASCADE"),
        sa.CheckConstraint("length(token_hash) = 32", name="ck_sessions_sha256_length"),
        sa.CheckConstraint("expires_at > created_at", name="ck_sessions_absolute_expiration"),
        sa.CheckConstraint("last_activity_at >= created_at AND last_activity_at < expires_at", name="ck_sessions_last_activity"),
        sa.CheckConstraint("idle_expires_at > last_activity_at AND idle_expires_at <= expires_at", name="ck_sessions_idle_expiration"),
    )
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)
    for column in ("user_id", "expires_at", "idle_expires_at"):
        op.create_index(f"ix_sessions_{column}", "sessions", [column])

    # PostgreSQL uses additive ALTER TABLE. SQLite uses Alembic's copy-and-move
    # implementation to add the named FK, retaining every original column/value.
    with op.batch_alter_table("job_applications") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_job_applications_user_id_users", "users", ["user_id"], ["id"], ondelete="RESTRICT")
        batch.create_index("ix_job_applications_user_id", ["user_id"])


def downgrade():
    raise RuntimeError("Authentication downgrade could lose ownership/accounts; it is intentionally disabled")
