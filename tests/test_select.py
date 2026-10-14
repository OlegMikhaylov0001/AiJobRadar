import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import load_scoring_config
from aijobradar.db.models import Job, LlmScore
from aijobradar.models import RawJob
from aijobradar.normalize import normalize
from aijobradar.select import select_digest

NOW = datetime(2026, 10, 14, 6, tzinfo=UTC)
CFG = load_scoring_config(Path(__file__).parent.parent / "config" / "scoring.yaml").model_copy(
    update={"threshold": 60.0, "top_n": 2, "explore_n": 1, "max_age_days": 14}
)


def _scored(session: Session, n: int, score: float, *, age_days: int = 1) -> Job:
    run = store.start_run(session, "fetch", NOW)
    profile, _ = store.add_profile_version(
        session, resume_md="R", targets_md="T", note=None, now=NOW
    )
    raw = RawJob(
        source="jobicy",
        source_job_id=str(n),
        source_url=f"https://jobicy.example/{n}",
        title=f"Engineer {n}",
        company=f"Company {n}",
        description_html="<p>x</p>",
    )
    job = store.insert_job(session, normalize(raw), run.id, NOW)
    store.attach_source(session, job.id, raw, NOW)
    job.posted_at = NOW - timedelta(days=age_days)
    row = LlmScore(
        job_id=job.id,
        run_id=run.id,
        input_hash=f"h{n}",
        prompt_version="v",
        model="m",
        profile_version_id=profile.id,
        status="ok",
        final_score=score,
        scored_at=NOW,
    )
    session.add(row)
    session.flush()
    store.mark_scored(session, [job], row.id)
    return job


def test_top_by_score_then_explore_below_threshold(session: Session) -> None:
    for n, score in enumerate((90, 75, 65, 40, 10)):
        _scored(session, n, score)
    picks = select_digest(session, cfg=CFG, now=NOW, rng=random.Random(1))
    assert [(p.slot, p.score.final_score) for p in picks][:2] == [("top", 90), ("top", 75)]
    assert [p.slot for p in picks] == ["top", "top", "explore"]
    assert picks[2].score.final_score is not None and picks[2].score.final_score < 60
    assert picks[0].sources[0].source == "jobicy"


def test_delivered_and_stale_jobs_are_never_picked(session: Session) -> None:
    sent = _scored(session, 1, 95)
    _scored(session, 2, 99, age_days=30)
    fresh = _scored(session, 3, 80)
    run = store.start_run(session, "daily", NOW)
    store.create_delivery(session, job_id=sent.id, run_id=run.id, slot="top", position=1, now=NOW)
    picks = select_digest(session, cfg=CFG, now=NOW, rng=random.Random(1))
    assert [p.job.id for p in picks] == [fresh.id]


def test_nothing_above_threshold_still_explores(session: Session) -> None:
    _scored(session, 1, 30)
    picks = select_digest(session, cfg=CFG, now=NOW, rng=random.Random(1))
    assert [p.slot for p in picks] == ["explore"]


def test_unscored_and_failed_jobs_are_ignored(session: Session) -> None:
    job = _scored(session, 1, 95)
    store.set_job_state(session, job, "score_failed")
    assert select_digest(session, cfg=CFG, now=NOW, rng=random.Random(1)) == []


@pytest.mark.parametrize("state", ["pending", "sent", "unknown"])
def test_a_delivery_in_any_state_excludes_the_job(session: Session, state: str) -> None:
    # pending/unknown: Telegram may have posted it; at most once beats a duplicate.
    job = _scored(session, 1, 95)
    run = store.start_run(session, "daily", NOW)
    delivery = store.create_delivery(
        session, job_id=job.id, run_id=run.id, slot="top", position=1, now=NOW
    )
    delivery.state = state
    session.flush()
    assert select_digest(session, cfg=CFG, now=NOW, rng=random.Random(1)) == []
