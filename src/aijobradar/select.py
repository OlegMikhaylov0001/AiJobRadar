"""Which scored jobs go into today's digest (spec §6.5). Pure selection, no sending."""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import ScoringConfig
from aijobradar.db.models import Delivery, Job, JobSource, LlmScore


@dataclass(frozen=True)
class Pick:
    job: Job
    score: LlmScore
    slot: str  # "top" | "explore"
    sources: list[JobSource]


def select_digest(
    session: Session, *, cfg: ScoringConfig, now: datetime, rng: random.Random
) -> list[Pick]:
    cutoff = now - timedelta(days=cfg.max_age_days)
    rows = session.execute(
        select(Job, LlmScore)
        .join(LlmScore, Job.llm_score_id == LlmScore.id)
        .where(
            Job.state == "scored",
            LlmScore.final_score.is_not(None),
            func.coalesce(Job.posted_at, Job.first_seen_at) >= cutoff,
            ~exists().where(Delivery.job_id == Job.id),  # a job is sent at most once
        )
        .order_by(LlmScore.final_score.desc(), Job.posted_at.desc().nulls_last(), Job.id)
    ).all()
    above: list[tuple[Job, LlmScore]] = []
    below: list[tuple[Job, LlmScore]] = []
    for job, score in rows:
        if score.final_score is None:  # the query already drops these; mypy needs the proof
            continue
        (above if score.final_score >= cfg.threshold else below).append((job, score))
    top = above[: cfg.top_n]
    # Random jobs below the threshold show what the ranking misses (spec §6.5).
    explore = rng.sample(below, min(cfg.explore_n, len(below)))
    return [
        Pick(job, score, slot, store.job_sources(session, job.id))
        for slot, chosen in (("top", top), ("explore", explore))
        for job, score in chosen
    ]
