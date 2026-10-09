from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import Job, ReviewItem, RuleDecision
from aijobradar.models import RawJob
from aijobradar.normalize import normalize

NOW = datetime(2026, 9, 30, 6, tzinfo=UTC)


def _job(session: Session, sid: str = "1") -> tuple[Job, object]:
    run = store.start_run(session, "fetch", NOW)
    raw = RawJob(
        source="s",
        source_job_id=sid,
        source_url=f"https://s/{sid}",
        title="Backend Engineer",
        company="Acme",
    )
    return store.insert_job(session, normalize(raw), run.id, NOW), run


def test_record_decision_and_state(session: Session) -> None:
    job, run = _job(session)
    store.record_rule_decision(
        session,
        job_id=job.id,
        run_id=run.id,  # type: ignore[attr-defined]
        rules_version="v1",
        hits={"R-STALE": "posted 2026-01-01"},
        now=NOW,
    )
    store.set_job_state(session, job, "rejected")
    decision = session.scalars(select(RuleDecision)).one()
    assert (decision.verdict, decision.rule_ids, decision.rules_version) == (
        "reject",
        ["R-STALE"],
        "v1",
    )
    assert decision.details == {"R-STALE": "posted 2026-01-01"}
    assert session.get(Job, job.id).state == "rejected"  # type: ignore[union-attr]


def test_pass_decision_has_empty_rule_ids(session: Session) -> None:
    job, run = _job(session)
    store.record_rule_decision(
        session,
        job_id=job.id,
        run_id=run.id,  # type: ignore[attr-defined]
        rules_version="v1",
        hits={},
        now=NOW,
    )
    decision = session.scalars(select(RuleDecision)).one()
    assert (decision.verdict, decision.rule_ids, decision.details) == ("pass", [], {})


def test_review_item_is_unique_per_kind(session: Session) -> None:
    job, run = _job(session)
    kw = {
        "job_id": job.id,
        "kind": "rule_rejected_sample",
        "run_id": run.id,  # type: ignore[attr-defined]
        "now": NOW,
    }
    assert store.add_review_item(session, **kw) is True  # type: ignore[arg-type]
    assert store.add_review_item(session, **kw) is False  # type: ignore[arg-type]
    assert len(session.scalars(select(ReviewItem)).all()) == 1


def test_jobs_in_state_filters_and_orders(session: Session) -> None:
    a, _ = _job(session, "a")
    b, _ = _job(session, "b")
    store.set_job_state(session, b, "rejected")
    assert [j.id for j in store.jobs_in_state(session, "new")] == [a.id]
