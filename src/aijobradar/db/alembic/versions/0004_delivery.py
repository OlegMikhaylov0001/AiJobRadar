"""deliveries, labels, kv; jobs.llm_score_id

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("llm_score_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_jobs_llm_score_id", "jobs", "llm_scores", ["llm_score_id"], ["id"], ondelete="SET NULL"
    )
    # Jobs scored before this column existed point at their own ok row when they have one.
    op.execute(
        "UPDATE jobs SET llm_score_id = s.id FROM llm_scores s "
        "WHERE s.job_id = jobs.id AND s.status = 'ok' AND jobs.state = 'scored'"
    )
    op.create_table(
        "deliveries",
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
        sa.Column("slot", sa.String(8), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("tg_message_id", sa.BigInteger(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", name="uq_deliveries_job"),
    )
    op.create_table(
        "labels",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(8), nullable=False),
        sa.Column("reason", sa.String(16), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_ref", name="uq_labels_source_ref"),
    )
    op.create_index("ix_labels_job_id", "labels", ["job_id"])
    op.create_table(
        "kv",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("kv")
    op.drop_index("ix_labels_job_id", table_name="labels")
    op.drop_table("labels")
    op.drop_table("deliveries")
    op.drop_constraint("fk_jobs_llm_score_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "llm_score_id")
