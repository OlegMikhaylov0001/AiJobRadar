"""Score pending jobs: cache first, then budgeted, parallel CLI calls; commit per job."""

import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import ScoringConfig
from aijobradar.db.models import Job, ProfileVersion
from aijobradar.scoring.claude_cli import ScoreResult, ScoreStatus
from aijobradar.scoring.combine import final_score
from aijobradar.scoring.prompt import input_hash, render_job, user_message

Scorer = Callable[[str], ScoreResult]
# A transient database error must not throw away a result that was already paid for.
_WRITE_ATTEMPTS = 2


@dataclass
class ScoringReport:
    ok: int = 0
    cached: int = 0
    invalid_output: int = 0
    refused: int = 0
    error: int = 0
    skipped_budget: int = 0
    parked: int = 0
    # Jobs not sent to the model because the database stopped accepting paid results.
    skipped_db_error: int = 0
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
    # Jobs whose rendered text is identical: they share one call and one paid result.
    followers: list[Job] = field(default_factory=list)

    @property
    def size(self) -> int:
        return 1 + len(self.followers)


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


def _newest_first(jobs: list[Job]) -> list[Job]:
    """Fresh postings first (undated ones last), so the per-run budget goes to live jobs."""

    def key(job: Job) -> tuple[bool, float, float, str]:
        posted = job.posted_at
        return (
            posted is None,
            -posted.timestamp() if posted is not None else 0.0,
            -job.first_seen_at.timestamp(),
            str(job.id),
        )

    return sorted(jobs, key=key)


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
    by_key: dict[str, _Pending] = {}
    for job in _newest_first(store.jobs_in_state(session, "pending_score")):
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
        elif key in by_key:
            by_key[key].followers.append(job)
        else:
            by_key[key] = _Pending(job, job.id, key, user_message(profile, rendered))
            todo.append(by_key[key])
        session.commit()

    cap = min(cfg.max_items_per_run, limit) if limit is not None else cfg.max_items_per_run
    report.skipped_budget += sum(p.size for p in todo[cap:])
    todo = todo[:cap]
    db_ok = True
    with ThreadPoolExecutor(max_workers=cfg.parallelism) as pool:
        for start in range(0, len(todo), cfg.parallelism):
            if not db_ok:
                # Fail closed: no more paid calls while their results cannot be stored.
                report.skipped_db_error += sum(p.size for p in todo[start:])
                break
            # A soft cap: checked before each wave, so one wave may go past it.
            if report.est_cost_usd >= cfg.max_est_cost_usd_per_run:
                report.skipped_budget += sum(p.size for p in todo[start:])
                break
            wave = todo[start : start + cfg.parallelism]
            # Workers only call the scorer; every database write stays in this thread, and each
            # paid result is stored as soon as it arrives, not after the whole wave.
            futures = {pool.submit(_safe, scorer, p.message): p for p in wave}
            for future in as_completed(futures):
                stored = _record(
                    session, futures[future], future.result(), run_id, ver, cfg, now, report
                )
                db_ok = db_ok and stored
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
) -> bool:
    report.est_cost_usd = round(report.est_cost_usd + result.est_cost_usd, 6)
    score = final_score(result.axes, cfg) if result.axes is not None else None
    parked = False
    for attempt in range(_WRITE_ATTEMPTS):
        try:
            parked = _write(session, pending, result, score, run_id, ver, cfg, now)
            break
        except IntegrityError as exc:
            session.rollback()
            report.first_error = report.first_error or type(exc).__name__
            # Safe only if another writer really stored an ok score for this key (a concurrent
            # run). Any other integrity error (e.g. a foreign key) lost the result: fail closed.
            if _adopt_existing_score(session, pending, ver):
                report.cached += pending.size
                return True
            report.error += 1
            return False
        except Exception as exc:
            session.rollback()
            if attempt + 1 == _WRITE_ATTEMPTS:
                report.error += 1
                report.first_error = report.first_error or type(exc).__name__
                return False
    if result.status is ScoreStatus.OK:
        report.ok += 1
        report.cached += len(pending.followers)
        return True
    if result.status is ScoreStatus.INVALID_OUTPUT:
        report.invalid_output += 1
    elif result.status is ScoreStatus.REFUSED:
        report.refused += 1
    else:
        report.error += 1
    if parked:
        report.parked += pending.size
    report.first_error = report.first_error or result.error
    return True


def _adopt_existing_score(session: Session, pending: _Pending, ver: _Key) -> bool:
    try:
        existing = store.find_ok_score(
            session,
            input_hash=pending.key,
            prompt_version=ver.prompt_version,
            model=ver.model,
            profile_version_id=ver.profile_version_id,
        )
        if existing is None:
            return False
        for job in (pending.job, *pending.followers):
            store.set_job_state(session, job, "scored")
        session.commit()
    except Exception:
        session.rollback()
        return False
    return True


def _write(
    session: Session,
    pending: _Pending,
    result: ScoreResult,
    score: float | None,
    run_id: uuid.UUID,
    ver: _Key,
    cfg: ScoringConfig,
    now: datetime,
) -> bool:
    """Store one paid result in its own transaction; True if the job was parked by it."""
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
    state: str | None = None
    if result.status is ScoreStatus.OK:
        state = "scored"
    elif result.status in (ScoreStatus.INVALID_OUTPUT, ScoreStatus.REFUSED):
        failed = store.count_failed_attempts(
            session,
            input_hash=pending.key,
            prompt_version=ver.prompt_version,
            model=ver.model,
            profile_version_id=ver.profile_version_id,
        )
        if failed >= cfg.max_attempts:
            state = "score_failed"  # park now, not one run later
    if state is not None:
        for job in (pending.job, *pending.followers):
            store.set_job_state(session, job, state)
    session.commit()  # a paid result must survive a later failure
    return state == "score_failed"
