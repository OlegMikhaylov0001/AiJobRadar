import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(String(16))
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class SourceRun(Base):
    __tablename__ = "source_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    item_count: Mapped[int] = mapped_column(Integer)
    invalid_items: Mapped[int] = mapped_column(Integer)
    out_of_scope: Mapped[int] = mapped_column(Integer)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_hash: Mapped[str] = mapped_column(String(64))
    company_raw: Mapped[str] = mapped_column(Text)
    company_norm: Mapped[str] = mapped_column(Text, index=True)
    title_raw: Mapped[str] = mapped_column(Text)
    title_norm: Mapped[str] = mapped_column(Text)
    location_text: Mapped[str | None] = mapped_column(Text)
    location_restrictions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    timezone_restrictions: Mapped[list[float] | None] = mapped_column(JSONB(none_as_null=True))
    employment_type: Mapped[str | None] = mapped_column(Text)
    seniority: Mapped[str | None] = mapped_column(Text)
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    salary_currency: Mapped[str | None] = mapped_column(String(8))
    salary_period: Mapped[str | None] = mapped_column(String(8))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description_text: Mapped[str] = mapped_column(Text)
    apply_url_canonical: Mapped[str] = mapped_column(Text, index=True)
    first_seen_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[str] = mapped_column(String(16), default="new")
    # The score this job was marked `scored` by: its own, a cached one, or a twin's.
    llm_score_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "llm_scores.id", ondelete="SET NULL", use_alter=True, name="fk_jobs_llm_score_id"
        )
    )


class JobSource(Base):
    __tablename__ = "job_sources"
    __table_args__ = (
        UniqueConstraint("source", "source_job_id", name="uq_job_sources_source_job"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(32))
    source_job_id: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RuleDecision(Base):
    __tablename__ = "rule_decisions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    rules_version: Mapped[str] = mapped_column(String(32))
    verdict: Mapped[str] = mapped_column(String(8))
    rule_ids: Mapped[list[str]] = mapped_column(JSONB)
    # Evidence per rule: third-party text and profile thresholds — DB only, never printed.
    details: Mapped[dict[str, str]] = mapped_column(JSONB)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReviewItem(Base):
    __tablename__ = "review_queue"
    __table_args__ = (UniqueConstraint("job_id", "kind", name="uq_review_queue_job_kind"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    added_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProfileVersion(Base):
    __tablename__ = "profile_versions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Personal: the candidate's resume and targets. DB only, never printed.
    resume_md: Mapped[str] = mapped_column(Text)
    targets_md: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)


class LlmScore(Base):
    __tablename__ = "llm_scores"
    __table_args__ = (
        # Cache and "never pay twice": one ok score per exact model input.
        Index(
            "uq_llm_scores_ok_key",
            "input_hash",
            "prompt_version",
            "model",
            "profile_version_id",
            unique=True,
            postgresql_where=text("status = 'ok'"),
        ),
        Index("ix_llm_scores_key", "input_hash", "prompt_version", "model", "profile_version_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    input_hash: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    profile_version_id: Mapped[int] = mapped_column(ForeignKey("profile_versions.id"))
    status: Mapped[str] = mapped_column(String(16))
    axes: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    final_score: Mapped[float | None] = mapped_column(Float)
    why_fit: Mapped[str | None] = mapped_column(Text)
    main_risk: Mapped[str | None] = mapped_column(Text)
    gaps: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    est_cost_usd: Mapped[float | None] = mapped_column(Float)
    raw_output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Delivery(Base):
    __tablename__ = "deliveries"
    # A job is sent at most once, ever.
    __table_args__ = (UniqueConstraint("job_id", name="uq_deliveries_job"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    slot: Mapped[str] = mapped_column(String(8))  # top | explore
    position: Mapped[int] = mapped_column(Integer)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)
    # pending: committed before the send · sent: Telegram confirmed · unknown: the send may or
    # may not have arrived. Every state excludes the job from later digests (at most once).
    state: Mapped[str] = mapped_column(String(8))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Label(Base):
    __tablename__ = "labels"
    # One label per Telegram button press, however many times the update is re-read.
    __table_args__ = (UniqueConstraint("source_ref", name="uq_labels_source_ref"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(8))  # fit | no_fit
    reason: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))  # telegram | ui | seed
    source_ref: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Kv(Base):
    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
