"""Baseline: the original job_applications schema, without ownership."""
from alembic import op
import sqlalchemy as sa

revision = "0001_legacy"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "job_applications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("company_name", sa.String(150), nullable=False),
        sa.Column("job_title", sa.String(200), nullable=False),
        sa.Column("location", sa.String(150), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("application_date", sa.Date(), nullable=True),
        sa.Column("job_description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    raise RuntimeError("Baseline downgrade would delete application records; it is intentionally disabled")
