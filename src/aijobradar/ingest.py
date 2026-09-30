import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import DedupConfig
from aijobradar.dedup import DedupOutcome, best_fuzzy_match
from aijobradar.normalize import NormalizedJob


def ingest(
    session: Session, job: NormalizedJob, *, run_id: uuid.UUID, now: datetime, cfg: DedupConfig
) -> DedupOutcome:
    raw = job.raw
    link = store.find_source(session, raw.source, raw.source_job_id)
    if link is not None:
        store.touch_source(session, link, now)
        return DedupOutcome.SEEN

    since = now - timedelta(days=cfg.match_window_days)
    match_id = store.find_job_id_by_url(session, job.apply_url_canonical, since)
    if match_id is None:
        candidates = store.company_candidates(session, job.company_norm, since)
        match = best_fuzzy_match(job.title_norm, candidates, cfg.title_similarity)
        match_id = match.job_id if match else None
    if match_id is not None:
        store.attach_source(session, match_id, raw, now)
        return DedupOutcome.MERGED

    row = store.insert_job(session, job, run_id, now)
    store.attach_source(session, row.id, raw, now)
    return DedupOutcome.NEW
