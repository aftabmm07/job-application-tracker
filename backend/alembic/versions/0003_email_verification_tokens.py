"""Add hashed email verification tokens without changing existing records."""
from alembic import op
import sqlalchemy as sa

revision = "0003_email_verification_tokens"
down_revision = "0002_auth_models"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("length(token_hash) = 32", name="ck_email_verification_tokens_sha256_length"),
        sa.CheckConstraint("expires_at > created_at", name="ck_email_verification_tokens_expiration"),
    )
    op.create_index("ix_email_verification_tokens_token_hash", "email_verification_tokens", ["token_hash"], unique=True)
    op.create_index("ix_email_verification_tokens_user_id", "email_verification_tokens", ["user_id"])


def downgrade():
    raise RuntimeError("Email verification downgrade would lose token state; it is intentionally disabled")
