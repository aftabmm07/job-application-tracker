"""Add dormant verification-email outbox metadata; no delivery is activated."""
from alembic import op
import sqlalchemy as sa

revision = "0004_email_verification_outbox"
down_revision = "0003_email_verification_tokens"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_verification_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_id", sa.Integer(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(32), nullable=False),
        sa.Column("key_version", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), server_default="queued", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.LargeBinary(16), nullable=True),
        sa.Column("fencing_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_category", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(["token_id"], ["email_verification_tokens.id"],
                                name="fk_email_verification_outbox_token_id", ondelete="CASCADE"),
        sa.UniqueConstraint("token_id", name="uq_email_verification_outbox_token_id"),
        sa.CheckConstraint("length(nonce) = 32", name="ck_email_verification_outbox_nonce_length"),
        sa.CheckConstraint("length(key_version) BETWEEN 1 AND 32", name="ck_email_verification_outbox_key_version"),
        sa.CheckConstraint("state IN ('queued', 'leased', 'delivered', 'cancelled', 'uncertain')",
                           name="ck_email_verification_outbox_state"),
        sa.CheckConstraint("expires_at > created_at", name="ck_email_verification_outbox_expiration"),
        sa.CheckConstraint("next_attempt_at >= created_at AND next_attempt_at <= expires_at",
                           name="ck_email_verification_outbox_schedule"),
        sa.CheckConstraint("attempts >= 0", name="ck_email_verification_outbox_attempts"),
        sa.CheckConstraint("fencing_generation >= 0", name="ck_email_verification_outbox_generation"),
        sa.CheckConstraint(
            "(state = 'leased' AND lease_owner IS NOT NULL AND length(lease_owner) = 16 "
            "AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'leased' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_email_verification_outbox_lease_state",
        ),
        sa.CheckConstraint(
            "failure_category IS NULL OR failure_category IN "
            "('transport', 'timeout', 'storage', 'configuration', 'uncertain', 'other')",
            name="ck_email_verification_outbox_failure_category",
        ),
    )
    op.create_index("ix_email_verification_outbox_due", "email_verification_outbox",
                    ["state", "next_attempt_at"])
    op.create_index("ix_email_verification_outbox_lease", "email_verification_outbox",
                    ["state", "lease_expires_at"])


def downgrade():
    raise RuntimeError("Outbox downgrade could lose delivery state; it is intentionally disabled")
