from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import Job, LlmScore, ProfileVersion, Run

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)


def _job_and_run(session: Session) -> tuple[Job, Run]:
    run = Run(kind="score", started_at=NOW)
    session.add(run)
    session.flush()
    job = Job(
        content_hash="h",
        company_raw="Acme",
        company_norm="acme",
        title_raw="Engineer",
        title_norm="engineer",
        description_text="text",
        apply_url_canonical="https://example.com/1",
        first_seen_run_id=run.id,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    session.add(job)
    session.flush()
    return job, run


def _profile(session: Session, resume_md: str = "# R") -> ProfileVersion:
    pv, _ = store.add_profile_version(
        session, resume_md=resume_md, targets_md="# T", note=None, now=NOW
    )
    return pv


def _score(job: Job, run: Run, pv: ProfileVersion, status: str) -> LlmScore:
    return LlmScore(
        job_id=job.id,
        run_id=run.id,
        input_hash="abc",
        prompt_version="v1",
        model="test-model",
        profile_version_id=pv.id,
        status=status,
        scored_at=NOW,
    )


def test_two_ok_scores_with_same_key_conflict(session: Session) -> None:
    job, run = _job_and_run(session)
    pv = _profile(session)
    session.add(_score(job, run, pv, "ok"))
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(_score(job, run, pv, "ok"))
        session.flush()


def test_error_scores_with_same_key_may_repeat(session: Session) -> None:
    job, run = _job_and_run(session)
    pv = _profile(session)
    session.add_all([_score(job, run, pv, "error"), _score(job, run, pv, "error")])
    session.flush()


def test_ok_score_may_follow_error_with_same_key(session: Session) -> None:
    job, run = _job_and_run(session)
    pv = _profile(session)
    session.add(_score(job, run, pv, "error"))
    session.flush()
    session.add(_score(job, run, pv, "ok"))
    session.flush()


def test_ok_scores_for_different_profile_versions_coexist(session: Session) -> None:
    job, run = _job_and_run(session)
    first, second = _profile(session), _profile(session, resume_md="# R2")
    assert first.id != second.id
    session.add_all([_score(job, run, first, "ok"), _score(job, run, second, "ok")])
    session.flush()
