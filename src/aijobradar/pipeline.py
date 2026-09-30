import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import httpx
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import DedupConfig
from aijobradar.dedup import DedupOutcome
from aijobradar.ingest import ingest
from aijobradar.models import SourceResult, SourceStatus
from aijobradar.normalize import normalize
from aijobradar.sources.base import Adapter, run_adapter

_STATUS_MARK = {SourceStatus.FAILED: "❌ ", SourceStatus.DEGRADED: "⚠️ "}


class RunStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class FetchReport:
    run_id: uuid.UUID
    status: RunStatus
    sources: list[SourceResult]
    outcomes: dict[DedupOutcome, int]


def run_status(results: Sequence[SourceResult]) -> RunStatus:
    if not results or all(r.status is SourceStatus.FAILED for r in results):
        return RunStatus.FAILED
    if any(
        r.status in (SourceStatus.FAILED, SourceStatus.DEGRADED) or r.ingest_errors for r in results
    ):
        return RunStatus.PARTIAL
    return RunStatus.OK


def _describe_ingest_error(exc: Exception) -> str:
    """One line without row values: SQLAlchemy's own message embeds the bound parameters."""
    if isinstance(exc, DBAPIError):
        first_line = str(exc.orig).splitlines()[0] if str(exc.orig) else ""
        return f"{type(exc.orig).__name__}: {first_line}"[:200]
    return type(exc).__name__


def _ingest_source(
    session: Session,
    result: SourceResult,
    run_id: uuid.UUID,
    now: datetime,
    dedup_cfg: DedupConfig,
    outcomes: Counter[DedupOutcome],
) -> None:
    """Store one source's records; a record the database rejects costs only that record."""
    first_error: str | None = None
    for raw in result.items:
        try:
            with session.begin_nested():  # savepoint: roll back this record, keep the run
                outcome = ingest(session, normalize(raw), run_id=run_id, now=now, cfg=dedup_cfg)
        except Exception as exc:
            result.ingest_errors += 1
            first_error = first_error or _describe_ingest_error(exc)
            continue
        outcomes[outcome] += 1
    if result.ingest_errors:
        note = f"ingest: {result.ingest_errors} not stored; first: {first_error}"
        result.error = f"{result.error}; {note}" if result.error else note


def run_fetch(
    session: Session,
    adapters: Sequence[Adapter],
    client: httpx.Client,
    *,
    now: datetime,
    dedup_cfg: DedupConfig,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FetchReport:
    """Fetch every source first (network only), then record and ingest in the database.

    `now` stamps the run start and every seen/first-seen time; `clock` gives the real finish time.
    """
    results = [run_adapter(adapter, client) for adapter in adapters]
    run = store.start_run(session, "fetch", now)
    outcomes: Counter[DedupOutcome] = Counter()
    for result in results:
        _ingest_source(session, result, run.id, now, dedup_cfg, outcomes)
        store.record_source_result(session, run.id, result)
    status = run_status(results)
    counts = {
        outcome.value: outcomes.get(outcome, 0)
        for outcome in (DedupOutcome.NEW, DedupOutcome.MERGED, DedupOutcome.SEEN)
    }
    counts["ingest_errors"] = sum(r.ingest_errors for r in results)
    store.finish_run(session, run, status.value, counts, clock())
    return FetchReport(run_id=run.id, status=status, sources=results, outcomes=dict(outcomes))


def format_report(report: FetchReport) -> str:
    lines = [f"Прогон: {report.status.value} ({report.run_id})"]
    for r in report.sources:
        line = (
            f"{r.source}: {_STATUS_MARK.get(r.status, '')}{r.status.value}, "
            f"записей {len(r.items)} (невалидных {r.invalid_items}, "
            f"вне области {r.out_of_scope}), {r.duration_ms} мс"
        )
        if r.ingest_errors:
            line += f", ошибок записи {r.ingest_errors}"
        if r.error:
            line += f" — {r.error}"
        lines.append(line)
    o = report.outcomes
    lines.append(
        f"Вакансии: новых {o.get(DedupOutcome.NEW, 0)}, "
        f"склеено с известными {o.get(DedupOutcome.MERGED, 0)}, "
        f"уже виденных {o.get(DedupOutcome.SEEN, 0)}, "
        f"ошибок записи {sum(r.ingest_errors for r in report.sources)}"
    )
    return "\n".join(lines)
