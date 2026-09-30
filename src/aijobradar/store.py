"""All database reads and writes. Nothing else in the package touches the ORM session."""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from aijobradar.db.models import Job, JobSource, Run, SourceRun
from aijobradar.dedup import Candidate
from aijobradar.models import RawJob, SourceResult
from aijobradar.normalize import NormalizedJob


def start_run(session: Session, kind: str, now: datetime) -> Run:
    run = Run(kind=kind, started_at=now, counts={})
    session.add(run)
    session.flush()
    return run


def finish_run(
    session: Session, run: Run, status: str, counts: dict[str, int], now: datetime
) -> None:
    run.status = status
    run.counts = counts
    run.finished_at = now
    session.flush()


def record_source_result(session: Session, run_id: uuid.UUID, result: SourceResult) -> None:
    session.add(
        SourceRun(
            run_id=run_id,
            source=result.source,
            status=result.status.value,
            item_count=len(result.items),
            invalid_items=result.invalid_items,
            out_of_scope=result.out_of_scope,
            http_status=result.http_status,
            error=result.error,
            duration_ms=result.duration_ms,
        )
    )
    session.flush()


def find_source(session: Session, source: str, source_job_id: str) -> JobSource | None:
    return session.scalars(
        select(JobSource).where(
            JobSource.source == source, JobSource.source_job_id == source_job_id
        )
    ).one_or_none()


def find_job_id_by_url(session: Session, url: str, since: datetime) -> uuid.UUID | None:
    return session.scalars(
        select(Job.id).where(Job.apply_url_canonical == url, Job.last_seen_at >= since).limit(1)
    ).first()


def company_candidates(session: Session, company_norm: str, since: datetime) -> list[Candidate]:
    rows = session.execute(
        select(Job.id, Job.title_norm).where(
            Job.company_norm == company_norm, Job.last_seen_at >= since
        )
    ).all()
    return [Candidate(job_id=row.id, title_norm=row.title_norm) for row in rows]


def insert_job(session: Session, job: NormalizedJob, run_id: uuid.UUID, now: datetime) -> Job:
    raw = job.raw
    row = Job(
        content_hash=job.content_hash,
        company_raw=raw.company,
        company_norm=job.company_norm,
        title_raw=raw.title,
        title_norm=job.title_norm,
        location_text=raw.location_text,
        location_restrictions=raw.location_restrictions,
        timezone_restrictions=raw.timezone_restrictions,
        employment_type=raw.employment_type,
        seniority=raw.seniority,
        salary_min=raw.salary_min,
        salary_max=raw.salary_max,
        salary_currency=raw.salary_currency,
        salary_period=raw.salary_period.value if raw.salary_period else None,
        posted_at=raw.posted_at,
        description_text=job.description_text,
        apply_url_canonical=job.apply_url_canonical,
        first_seen_run_id=run_id,
        first_seen_at=now,
        last_seen_at=now,
        state="new",
    )
    session.add(row)
    session.flush()  # later records in the same run must see this job when deduplicating
    return row


def attach_source(session: Session, job_id: uuid.UUID, raw: RawJob, now: datetime) -> None:
    session.add(
        JobSource(
            job_id=job_id,
            source=raw.source,
            source_job_id=raw.source_job_id,
            source_url=raw.source_url,
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    session.execute(update(Job).where(Job.id == job_id).values(last_seen_at=now))
    session.flush()


def touch_source(session: Session, link: JobSource, now: datetime) -> None:
    link.last_seen_at = now
    session.execute(update(Job).where(Job.id == link.job_id).values(last_seen_at=now))
    session.flush()
