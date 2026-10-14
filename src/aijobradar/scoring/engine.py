"""Score pending jobs: cache first, then budgeted, parallel CLI calls; commit per job."""

import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import ScoringConfig
from aijobradar.db.models import Job, ProfileVersion
from aijobradar.scoring.claude_cli import ScoreResult, ScoreStatus
from aijobradar.scoring.combine import final_score
from aijobradar.scoring.prompt import input_hash, render_job, user_message

Scorer = Callable[[str], ScoreResult]


@dataclass
class ScoringReport:
    ok: int = 0
    cached: int = 0
    invalid_output: int = 0
    refused: int = 0
    error: int = 0
    skipped_budget: int = 0
    parked: int = 0
    est_cost_usd: float = 0.0
    first_error: str | None = None

    @property
    def failed(self) -> int:
        return self.invalid_output + self.refused + self.error


@dataclass(frozen=True)
class _Pending:
    job: Job
    job_id: uuid.UUID
    key: str
    message: str


@dataclass(frozen=True)
class _Key:
    """The parts of the cache key that are the same for every job in a run."""

    prompt_version: str
    model: str
    profile_version_id: int


def _safe(scorer: Scorer, message: str) -> ScoreResult:
    """A raising scorer must not lose the other (already paid) results of its wave."""
    try:
        return scorer(message)
    except Exception as exc:
        # Only the class name: the message may contain job text.
        return ScoreResult(ScoreStatus.ERROR, None, None, None, 0.0, None, type(exc).__name__)


def score_pending(
    session: Session,
    *,
    run_id: uuid.UUID,
    profile: ProfileVersion,
    cfg: ScoringConfig,
    scorer: Scorer,
    now: datetime,
    limit: int | None = None,
) -> ScoringReport:
    report = ScoringReport()
    ver = _Key(cfg.prompt_version, cfg.model, profile.id)
    todo: list[_Pending] = []
    for job in store.jobs_in_state(session, "pending_score"):
        rendered = render_job(job, cfg.max_description_chars)
        key = input_hash(rendered)
        if (
            store.find_ok_score(
                session,
                input_hash=key,
                prompt_version=ver.prompt_version,
                model=ver.model,
                profile_version_id=ver.profile_version_id,
            )
            is not None
        ):
            store.set_job_state(session, job, "scored")
            report.cached += 1
        elif (
            store.count_failed_attempts(
                session,
                input_hash=key,
                prompt_version=ver.prompt_version,
                model=ver.model,
                profile_version_id=ver.profile_version_id,
            )
            >= cfg.max_attempts
        ):
            store.set_job_state(session, job, "score_failed")
            report.parked += 1
        else:
            todo.append(_Pending(job, job.id, key, user_message(profile, rendered)))
        session.commit()

    cap = min(cfg.max_items_per_run, limit) if limit is not None else cfg.max_items_per_run
    report.skipped_budget += max(0, len(todo) - cap)
    todo = todo[:cap]
    with ThreadPoolExecutor(max_workers=cfg.parallelism) as pool:
        for start in range(0, len(todo), cfg.parallelism):
            if report.est_cost_usd >= cfg.max_est_cost_usd_per_run:
                report.skipped_budget += len(todo) - start
                break
            wave = todo[start : start + cfg.parallelism]
            # Workers only call the scorer; every database write stays in this thread.
            results = list(pool.map(lambda p: _safe(scorer, p.message), wave))
            for pending, result in zip(wave, results, strict=True):
                _record(session, pending, result, run_id, ver, cfg, now, report)
    return report


def _record(
    session: Session,
    pending: _Pending,
    result: ScoreResult,
    run_id: uuid.UUID,
    ver: _Key,
    cfg: ScoringConfig,
    now: datetime,
    report: ScoringReport,
) -> None:
    report.est_cost_usd = round(report.est_cost_usd + result.est_cost_usd, 6)
    score = final_score(result.axes, cfg) if result.axes is not None else None
    try:
        store.record_llm_score(
            session,
            job_id=pending.job_id,
            run_id=run_id,
            input_hash=pending.key,
            prompt_version=ver.prompt_version,
            model=ver.model,
            profile_version_id=ver.profile_version_id,
            result=result,
            final_score=score,
            now=now,
        )
        if result.status is ScoreStatus.OK:
            store.set_job_state(session, pending.job, "scored")
        session.commit()  # a paid result must survive a later failure
    except Exception as exc:
        session.rollback()
        report.error += 1
        report.first_error = report.first_error or type(exc).__name__
        return
    if result.status is ScoreStatus.OK:
        report.ok += 1
    else:
        setattr(report, result.status.value, getattr(report, result.status.value) + 1)
        report.first_error = report.first_error or result.error
