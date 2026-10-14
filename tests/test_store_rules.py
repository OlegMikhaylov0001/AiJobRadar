from datetime import UTC, datetime, timedelta

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


def _decide(session: Session, job: Job, run: object, version: str, now: datetime) -> None:
    store.record_rule_decision(
        session,
        job_id=job.id,
        run_id=run.id,  # type: ignore[attr-defined]
        rules_version=version,
        hits={"R-STALE": "x"},
        now=now,
    )


def test_recheck_reopens_only_rejections_of_other_versions(session: Session) -> None:
    old, run = _job(session, "old")
    cur, _ = _job(session, "cur")
    passed, _ = _job(session, "passed")
    for job, version, hits, state in (
        (old, "v1", {"R-STALE": "x"}, "rejected"),
        (cur, "v2", {"R-STALE": "x"}, "rejected"),
        (passed, "v1", {}, "pending_score"),
    ):
        store.record_rule_decision(
            session,
            job_id=job.id,
            run_id=run.id,  # type: ignore[attr-defined]
            rules_version=version,
            hits=hits,
            now=NOW,
        )
        store.set_job_state(session, job, state)
    assert store.reopen_rejected_from_other_versions(session, "v2") == 1
    assert session.get(Job, old.id).state == "new"  # type: ignore[union-attr]
    assert session.get(Job, cur.id).state == "rejected"  # type: ignore[union-attr]
    assert session.get(Job, passed.id).state == "pending_score"  # type: ignore[union-attr]


def test_recheck_looks_at_the_latest_decision_only(session: Session) -> None:
    newer_is_old, run = _job(session, "a")
    newer_is_current, _ = _job(session, "b")
    earlier, later = NOW, NOW + timedelta(hours=1)
    _decide(session, newer_is_old, run, "v2", earlier)
    _decide(session, newer_is_old, run, "v1", later)
    _decide(session, newer_is_current, run, "v1", earlier)
    _decide(session, newer_is_current, run, "v2", later)
    store.set_job_state(session, newer_is_old, "rejected")
    store.set_job_state(session, newer_is_current, "rejected")
    assert store.reopen_rejected_from_other_versions(session, "v2") == 1
    assert session.get(Job, newer_is_old.id).state == "new"  # type: ignore[union-attr]
    assert session.get(Job, newer_is_current.id).state == "rejected"  # type: ignore[union-attr]


def test_recheck_leaves_a_rejection_without_any_rule_decision_alone(session: Session) -> None:
    """Nothing says which rules version it was judged by, so it is not reopened."""
    job, _ = _job(session, "undecided")
    store.set_job_state(session, job, "rejected")
    assert store.reopen_rejected_from_other_versions(session, "v2") == 0
    assert session.get(Job, job.id).state == "rejected"  # type: ignore[union-attr]
