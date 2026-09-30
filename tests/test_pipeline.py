import json
from datetime import UTC, datetime

import httpx
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from aijobradar.config import AppConfig, DedupConfig
from aijobradar.db.models import Run, SourceRun
from aijobradar.dedup import DedupOutcome
from aijobradar.models import SourceResult, SourceStatus
from aijobradar.pipeline import RunStatus, format_report, run_fetch, run_status
from aijobradar.sources import build_adapters
from aijobradar.sources.himalayas import BASE_URL, HimalayasAdapter
from aijobradar.sources.jobicy import API_URL, JobicyAdapter
from aijobradar.sources.wwr import FEED_URL, WwrAdapter
from tests.conftest import fixture_path

NOW = datetime(2026, 9, 30, 6, tzinfo=UTC)


def _r(status: SourceStatus) -> SourceResult:
    return SourceResult(source="s", status=status)


def test_run_status_rules() -> None:
    assert run_status([]) is RunStatus.FAILED
    assert run_status([_r(SourceStatus.FAILED)] * 2) is RunStatus.FAILED
    assert run_status([_r(SourceStatus.OK), _r(SourceStatus.FAILED)]) is RunStatus.PARTIAL
    assert run_status([_r(SourceStatus.OK), _r(SourceStatus.DEGRADED)]) is RunStatus.PARTIAL
    assert run_status([_r(SourceStatus.OK), _r(SourceStatus.EMPTY)]) is RunStatus.OK


def test_build_adapters_respects_enabled() -> None:
    cfg = AppConfig.model_validate({"jobicy": {"enabled": False}})
    assert [a.name for a in build_adapters(cfg)] == ["himalayas", "wwr"]


def _mock_sources(jobicy_status: int = 200) -> None:
    pages = [
        json.loads(fixture_path("himalayas", n).read_text()) for n in ("page1.json", "page2.json")
    ]
    respx.get(BASE_URL).mock(side_effect=[httpx.Response(200, json=p) for p in pages])
    respx.get(API_URL).mock(
        return_value=httpx.Response(
            jobicy_status, json=json.loads(fixture_path("jobicy", "engineering.json").read_text())
        )
    )
    respx.get(FEED_URL.format(slug="remote-full-stack-programming-jobs")).mock(
        return_value=httpx.Response(200, content=fixture_path("wwr", "fullstack.rss").read_bytes())
    )


def _adapters() -> list[object]:
    return [
        HimalayasAdapter(now=lambda: NOW),
        JobicyAdapter(),
        WwrAdapter(feeds=("remote-full-stack-programming-jobs",)),
    ]


@respx.mock
def test_run_fetch_end_to_end_with_cross_source_dedup(session: Session) -> None:
    _mock_sources()
    with httpx.Client() as client:
        report = run_fetch(
            session,
            _adapters(),
            client,
            now=NOW,  # type: ignore[arg-type]
            dedup_cfg=DedupConfig(),
        )
    assert report.status is RunStatus.OK
    # himalayas: Acme, Northwind, Initech x2 (collapsed) -> 3 new + 1 merged
    # jobicy: Umbrella, Hooli -> 2 new
    # wwr: Northwind + Acme already known from himalayas -> 2 merged
    assert report.outcomes == {DedupOutcome.NEW: 5, DedupOutcome.MERGED: 3}
    run = session.get(Run, report.run_id)
    assert run is not None and run.status == "ok" and run.finished_at == NOW
    assert run.counts == {"new": 5, "merged": 3, "seen": 0}
    statuses = {r.source: r.status for r in session.scalars(select(SourceRun))}
    assert statuses == {"himalayas": "ok", "jobicy": "ok", "wwr": "ok"}


@respx.mock
def test_second_run_sees_everything_again(session: Session) -> None:
    for _ in range(2):
        _mock_sources()
        with httpx.Client() as client:
            report = run_fetch(
                session,
                _adapters(),
                client,
                now=NOW,  # type: ignore[arg-type]
                dedup_cfg=DedupConfig(),
            )
    assert report.outcomes == {DedupOutcome.SEEN: 8}


@respx.mock
def test_failed_source_is_reported_not_hidden(session: Session) -> None:
    _mock_sources(jobicy_status=503)
    with httpx.Client() as client:
        report = run_fetch(
            session,
            _adapters(),
            client,
            now=NOW,  # type: ignore[arg-type]
            dedup_cfg=DedupConfig(),
        )
    assert report.status is RunStatus.PARTIAL
    text = format_report(report)
    assert "jobicy: ❌ failed" in text
    assert "engineering: HTTP 503" in text
    assert "himalayas: ok" in text
    assert "Прогон: partial" in text


def test_format_report_lines() -> None:
    import uuid

    from aijobradar.pipeline import FetchReport

    report = FetchReport(
        run_id=uuid.UUID(int=1),
        status=RunStatus.PARTIAL,
        sources=[
            SourceResult(
                source="himalayas",
                status=SourceStatus.OK,
                invalid_items=1,
                out_of_scope=12,
                duration_ms=1500,
            ),
            SourceResult(
                source="wwr",
                status=SourceStatus.DEGRADED,
                error="remote-programming-jobs: HTTP 403",
            ),
        ],
        outcomes={DedupOutcome.NEW: 3, DedupOutcome.MERGED: 1},
    )
    assert format_report(report) == "\n".join(
        [
            "Прогон: partial (00000000-0000-0000-0000-000000000001)",
            "himalayas: ok, записей 0 (невалидных 1, вне области 12), 1500 мс",
            "wwr: ⚠️ degraded, записей 0 (невалидных 0, вне области 0), 0 мс"
            " — remote-programming-jobs: HTTP 403",
            "Вакансии: новых 3, склеено с известными 1, уже виденных 0",
        ]
    )
