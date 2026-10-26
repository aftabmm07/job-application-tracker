"""Add optional private profile fields to existing users."""
from alembic import op
import sqlalchemy as sa

revision = "0005_user_profile"
down_revision = "0004_email_verification_outbox"
branch_labels = None
depends_on = None


def upgrade():
    for name, length in (
        ("full_name", 120),
        ("professional_headline", 160),
        ("location", 120),
        ("linkedin_url", 500),
        ("github_url", 500),
        ("portfolio_url", 500),
    ):
        op.add_column("users", sa.Column(name, sa.String(length), nullable=True))


def downgrade():
    raise RuntimeError("Profile downgrade could discard user data; it is intentionally disabled")
