from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import DedupConfig
from aijobradar.db.models import Job, JobSource
from aijobradar.dedup import DedupOutcome
from aijobradar.ingest import ingest
from aijobradar.models import RawJob
from aijobradar.normalize import normalize

NOW = datetime(2026, 9, 30, 6, tzinfo=UTC)
CFG = DedupConfig()


def _raw(
    source: str, sid: str, title: str, company: str = "Initech", url: str | None = None
) -> RawJob:
    return RawJob(
        source=source,
        source_job_id=sid,
        source_url=url or f"https://{source}/{sid}",
        title=title,
        company=company,
        description_html="<p>x</p>",
    )


def _ingest(session: Session, raw: RawJob, now: datetime = NOW) -> DedupOutcome:
    run = store.start_run(session, "fetch", now)
    return ingest(session, normalize(raw), run_id=run.id, now=now, cfg=CFG)


def _count(session: Session, model: type[Job] | type[JobSource]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_new_job_is_inserted_with_source_link(session: Session) -> None:
    assert _ingest(session, _raw("himalayas", "1", "Full Stack Engineer")) is DedupOutcome.NEW
    job = session.scalars(select(Job)).one()
    assert (job.company_norm, job.title_norm, job.state) == ("initech", "fullstack engineer", "new")
    link = session.scalars(select(JobSource)).one()
    assert (link.job_id, link.source, link.source_job_id) == (job.id, "himalayas", "1")


def test_same_source_id_again_is_seen_and_touched(session: Session) -> None:
    _ingest(session, _raw("jobicy", "7", "QA Engineer"))
    later = NOW + timedelta(days=1)
    assert _ingest(session, _raw("jobicy", "7", "QA Engineer"), later) is DedupOutcome.SEEN
    assert _count(session, Job) == 1
    assert session.scalars(select(Job)).one().last_seen_at == later
    assert session.scalars(select(JobSource)).one().last_seen_at == later


def test_other_source_same_company_similar_title_is_merged(session: Session) -> None:
    _ingest(session, _raw("himalayas", "1", "Integrations Engineer", "Northwind Docs"))
    outcome = _ingest(session, _raw("wwr", "a", "Integration Engineer", "Northwind Docs, Inc."))
    assert outcome is DedupOutcome.MERGED
    assert (_count(session, Job), _count(session, JobSource)) == (1, 2)


def test_per_country_variants_collapse_into_one_job(session: Session) -> None:
    _ingest(session, _raw("himalayas", "2001", "Full Stack Engineer"))
    assert _ingest(session, _raw("himalayas", "2002", "Full Stack Engineer")) is (
        DedupOutcome.MERGED
    )
    assert _count(session, Job) == 1


def test_same_canonical_url_is_merged_even_with_different_title(session: Session) -> None:
    _ingest(session, _raw("a", "1", "Engineer", url="https://jobs.example/42?utm_source=x"))
    outcome = _ingest(
        session,
        _raw("b", "9", "Software Engineer II", "Other Name", url="https://jobs.example/42/"),
    )
    assert outcome is DedupOutcome.MERGED


def test_different_seniority_is_a_new_job(session: Session) -> None:
    _ingest(session, _raw("jobicy", "1", "Senior Backend Engineer"))
    assert _ingest(session, _raw("jobicy", "2", "Backend Engineer")) is DedupOutcome.NEW


def test_match_outside_window_is_new(session: Session) -> None:
    _ingest(session, _raw("jobicy", "1", "Full Stack Engineer"), NOW - timedelta(days=61))
    assert _ingest(session, _raw("wwr", "x", "Full Stack Engineer")) is DedupOutcome.NEW


def test_match_inside_window_is_merged(session: Session) -> None:
    _ingest(session, _raw("jobicy", "1", "Full Stack Engineer"), NOW - timedelta(days=59))
    assert _ingest(session, _raw("wwr", "x", "Full Stack Engineer")) is DedupOutcome.MERGED


def test_titles_differing_only_in_parenthesized_stack_are_not_merged(session: Session) -> None:
    company = "Acme Staffing AB"
    first = _raw("wwr", "1", "Senior Backend Developer (Node.js / Nest.js)", company)
    second = _raw("wwr", "2", "Senior Backend Developer (Python)", company)
    assert _ingest(session, first) is DedupOutcome.NEW
    assert _ingest(session, second) is DedupOutcome.NEW
    assert _count(session, Job) == 2
