"""profile versions and llm scores

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEY = ["input_hash", "prompt_version", "model", "profile_version_id"]


def upgrade() -> None:
    op.create_table(
        "profile_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resume_md", sa.Text(), nullable=False),
        sa.Column("targets_md", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_table(
        "llm_scores",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column(
            "profile_version_id",
            sa.BigInteger(),
            sa.ForeignKey("profile_versions.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("axes", postgresql.JSONB(), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("why_fit", sa.Text(), nullable=True),
        sa.Column("main_risk", sa.Text(), nullable=True),
        sa.Column("gaps", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("est_cost_usd", sa.Float(), nullable=True),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_llm_scores_job_id", "llm_scores", ["job_id"])
    op.create_index("ix_llm_scores_key", "llm_scores", _KEY)
    op.create_index(
        "uq_llm_scores_ok_key",
        "llm_scores",
        _KEY,
        unique=True,
        postgresql_where=sa.text("status = 'ok'"),
    )


def downgrade() -> None:
    op.drop_index("uq_llm_scores_ok_key", table_name="llm_scores")
    op.drop_index("ix_llm_scores_key", table_name="llm_scores")
    op.drop_index("ix_llm_scores_job_id", table_name="llm_scores")
    op.drop_table("llm_scores")
    op.drop_table("profile_versions")
