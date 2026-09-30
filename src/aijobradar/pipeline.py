import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import httpx
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
    if any(r.status in (SourceStatus.FAILED, SourceStatus.DEGRADED) for r in results):
        return RunStatus.PARTIAL
    return RunStatus.OK


def run_fetch(
    session: Session,
    adapters: Sequence[Adapter],
    client: httpx.Client,
    *,
    now: datetime,
    dedup_cfg: DedupConfig,
) -> FetchReport:
    run = store.start_run(session, "fetch", now)
    results: list[SourceResult] = []
    outcomes: Counter[DedupOutcome] = Counter()
    for adapter in adapters:
        result = run_adapter(adapter, client)
        store.record_source_result(session, run.id, result)
        for raw in result.items:
            outcomes[ingest(session, normalize(raw), run_id=run.id, now=now, cfg=dedup_cfg)] += 1
        results.append(result)
    status = run_status(results)
    counts = {
        outcome.value: outcomes.get(outcome, 0)
        for outcome in (DedupOutcome.NEW, DedupOutcome.MERGED, DedupOutcome.SEEN)
    }
    store.finish_run(session, run, status.value, counts, now)
    return FetchReport(run_id=run.id, status=status, sources=results, outcomes=dict(outcomes))


def format_report(report: FetchReport) -> str:
    lines = [f"Прогон: {report.status.value} ({report.run_id})"]
    for r in report.sources:
        line = (
            f"{r.source}: {_STATUS_MARK.get(r.status, '')}{r.status.value}, "
            f"записей {len(r.items)} (невалидных {r.invalid_items}, "
            f"вне области {r.out_of_scope}), {r.duration_ms} мс"
        )
        if r.error:
            line += f" — {r.error}"
        lines.append(line)
    o = report.outcomes
    lines.append(
        f"Вакансии: новых {o.get(DedupOutcome.NEW, 0)}, "
        f"склеено с известными {o.get(DedupOutcome.MERGED, 0)}, "
        f"уже виденных {o.get(DedupOutcome.SEEN, 0)}"
    )
    return "\n".join(lines)
