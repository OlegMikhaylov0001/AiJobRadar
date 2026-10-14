from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import Job, Run, SourceRun
from aijobradar.models import RawJob
from aijobradar.normalize import normalize

NOW = datetime(2026, 10, 14, 6, tzinfo=UTC)


def _job(session: Session, n: int = 0) -> tuple[Job, Run]:
    run = store.start_run(session, "fetch", NOW)
    raw = RawJob(
        source="himalayas",
        source_job_id=f"h-{n}",
        source_url=f"https://himalayas.example/jobs/{n}",
        title=f"Engineer {n}",
        company=f"Company {n}",
        description_html="<p>x</p>",
    )
    job = store.insert_job(session, normalize(raw), run.id, NOW)
    store.attach_source(session, job.id, raw, NOW)
    return job, run


def test_delivery_is_unique_per_job(session: Session) -> None:
    job, run = _job(session)
    first = store.create_delivery(
        session, job_id=job.id, run_id=run.id, slot="top", position=1, now=NOW
    )
    assert first.id is not None
    with pytest.raises(IntegrityError), session.begin_nested():
        store.create_delivery(
            session, job_id=job.id, run_id=run.id, slot="top", position=2, now=NOW
        )


def test_label_is_recorded_once_per_source_ref(session: Session) -> None:
    job, _ = _job(session)
    assert store.add_label(
        session, job_id=job.id, label="fit", source="telegram", source_ref="cb-1", now=NOW
    )
    assert not store.add_label(
        session, job_id=job.id, label="no_fit", source="telegram", source_ref="cb-1", now=NOW
    )


def test_kv_round_trip(session: Session) -> None:
    assert store.get_kv(session, "telegram_update_offset") is None
    store.set_kv(session, "telegram_update_offset", "10")
    store.set_kv(session, "telegram_update_offset", "11")
    assert store.get_kv(session, "telegram_update_offset") == "11"


def test_job_sources_are_listed(session: Session) -> None:
    job, _ = _job(session)
    assert [s.source for s in store.job_sources(session, job.id)] == ["himalayas"]


def test_last_source_success_ignores_failed_runs(session: Session) -> None:
    assert store.last_source_success(session, "himalayas") is None
    for hours_ago, status in ((30, "ok"), (5, "failed")):
        run = store.start_run(session, "fetch", NOW - timedelta(hours=hours_ago))
        session.add(
            SourceRun(
                run_id=run.id,
                source="himalayas",
                status=status,
                item_count=0,
                invalid_items=0,
                out_of_scope=0,
                http_status=None,
                error=None,
                duration_ms=1,
            )
        )
    session.flush()
    assert store.last_source_success(session, "himalayas") == NOW - timedelta(hours=30)
