import json
import random
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from aijobradar.config import AppConfig, DedupConfig
from aijobradar.db.models import Job, Run, SourceRun
from aijobradar.dedup import DedupOutcome
from aijobradar.models import RawJob, SourceResult, SourceStatus
from aijobradar.normalize import NormalizedJob
from aijobradar.pipeline import FetchReport, RunStatus, format_report, run_fetch, run_status
from aijobradar.rules.engine import RuleFn, RulesReport, apply_rules
from aijobradar.sources import build_adapters
from aijobradar.sources.base import Adapter, Fetched
from aijobradar.sources.himalayas import SEARCH_URL, HimalayasAdapter
from aijobradar.sources.jobicy import API_URL, JobicyAdapter
from aijobradar.sources.wwr import FEED_URL, WwrAdapter
from tests.conftest import fixture_path
from tests.rules_support import make_ctx

NOW = datetime(2026, 9, 30, 6, tzinfo=UTC)
FINISHED = datetime(2026, 9, 30, 6, 5, tzinfo=UTC)


def _r(status: SourceStatus) -> SourceResult:
    return SourceResult(source="s", status=status)


def test_run_status_rules() -> None:
    assert run_status([]) is RunStatus.FAILED
    assert run_status([_r(SourceStatus.FAILED)] * 2) is RunStatus.FAILED
    assert run_status([_r(SourceStatus.OK), _r(SourceStatus.FAILED)]) is RunStatus.PARTIAL
    assert run_status([_r(SourceStatus.OK), _r(SourceStatus.DEGRADED)]) is RunStatus.PARTIAL
    assert run_status([_r(SourceStatus.OK), _r(SourceStatus.EMPTY)]) is RunStatus.OK


def test_run_status_is_failed_when_no_record_reached_the_database() -> None:
    job = RawJob(source="s", source_job_id="1", source_url="https://x/1", title="T", company="C")
    stored_none = SourceResult(source="s", status=SourceStatus.OK, items=[job], ingest_errors=1)
    stored_one = SourceResult(source="s", status=SourceStatus.OK, items=[job, job], ingest_errors=1)
    assert run_status([stored_none, _r(SourceStatus.FAILED)]) is RunStatus.FAILED
    assert run_status([stored_none, _r(SourceStatus.EMPTY)]) is RunStatus.FAILED
    assert run_status([stored_one]) is RunStatus.PARTIAL


def test_build_adapters_respects_enabled() -> None:
    cfg = AppConfig.model_validate({"jobicy": {"enabled": False}})
    assert [a.name for a in build_adapters(cfg)] == ["himalayas", "wwr"]


def _mock_sources(jobicy_status: int = 200) -> None:
    pages = [
        json.loads(fixture_path("himalayas", n).read_text()) for n in ("page1.json", "page2.json")
    ]
    respx.get(SEARCH_URL).mock(side_effect=[httpx.Response(200, json=p) for p in pages])
    respx.get(API_URL).mock(
        return_value=httpx.Response(
            jobicy_status, json=json.loads(fixture_path("jobicy", "engineering.json").read_text())
        )
    )
    respx.get(FEED_URL.format(slug="remote-full-stack-programming-jobs")).mock(
        return_value=httpx.Response(200, content=fixture_path("wwr", "fullstack.rss").read_bytes())
    )


def _adapters() -> list[Adapter]:
    return [
        HimalayasAdapter(now=lambda: NOW, page_delay_s=0),
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
            now=NOW,
            dedup_cfg=DedupConfig(),
            clock=lambda: FINISHED,
        )
    assert report.status is RunStatus.OK
    # himalayas: Acme, Northwind, Initech x2 (collapsed) -> 3 new + 1 merged
    # jobicy: Umbrella, Hooli -> 2 new
    # wwr: Northwind + Acme already known from himalayas -> 2 merged
    assert report.outcomes == {DedupOutcome.NEW: 5, DedupOutcome.MERGED: 3}
    run = session.get(Run, report.run_id)
    assert run is not None and run.status == "ok" and run.finished_at == FINISHED
    assert run.counts == {"new": 5, "merged": 3, "seen": 0, "ingest_errors": 0}
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
                now=NOW,
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
            now=NOW,
            dedup_cfg=DedupConfig(),
        )
    assert report.status is RunStatus.PARTIAL
    text = format_report(report)
    assert "jobicy: ❌ failed" in text
    assert "engineering: HTTP 503" in text
    assert "himalayas: ok" in text
    assert "Прогон: partial" in text


def test_format_report_lines() -> None:
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
            "Вакансии: новых 3, склеено с известными 1, уже виденных 0, ошибок записи 0",
        ]
    )


def test_format_report_shows_ingest_errors() -> None:
    report = FetchReport(
        run_id=uuid.UUID(int=1),
        status=RunStatus.PARTIAL,
        sources=[
            SourceResult(
                source="jobicy",
                status=SourceStatus.OK,
                duration_ms=1830,
                ingest_errors=1,
                error="ingest: 1 not stored; first: DataError",
            ),
            SourceResult(source="wwr", status=SourceStatus.OK, ingest_errors=2),
        ],
        outcomes={DedupOutcome.NEW: 4},
    )
    lines = format_report(report).split("\n")
    assert lines[1] == (
        "jobicy: ok, записей 0 (невалидных 0, вне области 0), 1830 мс, ошибок записи 1"
        " — ingest: 1 not stored; first: DataError"
    )
    assert lines[2].endswith("0 мс, ошибок записи 2")
    assert lines[3] == "Вакансии: новых 4, склеено с известными 0, уже виденных 0, ошибок записи 3"


@respx.mock
def test_ingest_failure_of_one_record_is_isolated_and_counted(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aijobradar.pipeline as pipeline

    real_ingest = pipeline.ingest
    failed: list[str] = []

    def flaky(session: Session, job: NormalizedJob, **kwargs: Any) -> DedupOutcome:
        if job.raw.source == "jobicy" and not failed:
            failed.append(job.raw.source_job_id)
            raise RuntimeError("boom")
        return real_ingest(session, job, **kwargs)

    monkeypatch.setattr(pipeline, "ingest", flaky)
    _mock_sources()
    with httpx.Client() as client:
        report = run_fetch(session, _adapters(), client, now=NOW, dedup_cfg=DedupConfig())
    assert len(failed) == 1
    assert report.status is RunStatus.PARTIAL
    assert report.outcomes == {DedupOutcome.NEW: 4, DedupOutcome.MERGED: 3}
    run = session.get(Run, report.run_id)
    assert run is not None and run.status == "partial"
    assert run.counts == {"new": 4, "merged": 3, "seen": 0, "ingest_errors": 1}
    rows = {r.source: r for r in session.scalars(select(SourceRun))}
    assert set(rows) == {"himalayas", "jobicy", "wwr"}
    assert rows["jobicy"].error is not None and "RuntimeError" in rows["jobicy"].error
    assert rows["himalayas"].error is None
    text = format_report(report)
    assert "ошибок записи 1" in text and "Прогон: partial" in text


@dataclass
class _OneRecordAdapter:
    """Yields jobs whose `source` overflows the job_sources column: a real Postgres error."""

    records: tuple[str, ...] = ("bad-source", "fine")
    name: str = "fake"

    def fetch(self, client: httpx.Client) -> Fetched:
        return Fetched(list(self.records))

    def parse_record(self, record: Any) -> RawJob | None:
        source = "s" * 40 if record.startswith("bad-source") else "fake"
        return RawJob(
            source=source,
            source_job_id=record,
            source_url=f"https://x/{record}",
            title=f"Engineer {record}",
            company=f"Company {record}",
        )


def test_real_database_error_rolls_back_only_that_record(session: Session) -> None:
    with httpx.Client() as client:
        report = run_fetch(session, [_OneRecordAdapter()], client, now=NOW, dedup_cfg=DedupConfig())
    assert report.status is RunStatus.PARTIAL
    assert report.outcomes == {DedupOutcome.NEW: 1}
    assert report.sources[0].ingest_errors == 1
    assert session.scalars(select(Job.company_raw)).all() == ["Company fine"]  # no orphan job row
    source_run = session.scalars(select(SourceRun)).one()
    assert source_run.error is not None and "\n" not in source_run.error
    assert "ingest: 1 not stored" in source_run.error


def test_run_where_no_record_was_stored_is_failed_and_committed_as_such(session: Session) -> None:
    adapter = _OneRecordAdapter(records=("bad-source-1", "bad-source-2"))
    with httpx.Client() as client:
        report = run_fetch(session, [adapter], client, now=NOW, dedup_cfg=DedupConfig())
    assert report.status is RunStatus.FAILED  # exit code 1 in the CLI, not a quiet partial
    run = session.get(Run, report.run_id)
    assert run is not None and run.status == "failed"
    assert run.counts == {"new": 0, "merged": 0, "seen": 0, "ingest_errors": 2}


@respx.mock
def test_run_with_rules_filters_and_reports(session: Session) -> None:
    _mock_sources()
    with httpx.Client() as client:
        report = run_fetch(
            session,
            _adapters(),
            client,
            now=NOW,
            dedup_cfg=DedupConfig(),
            rules_ctx=make_ctx(),
            rng=random.Random(0),
        )
    # Acme (US/Canada after merge) and Initech (NL+UK after merge) are geo-rejected;
    # Northwind (worldwide wins on merge), Umbrella (Europe), Hooli (anywhere) pass.
    assert report.rules is not None
    assert (report.rules.evaluated, report.rules.rejected, report.rules.sampled) == (5, 2, 2)
    assert report.rules.by_rule == {"R-GEO-COUNTRY-ONLY": 2}
    run = session.get(Run, report.run_id)
    assert run is not None
    assert run.counts["rules_rejected"] == 2 and run.counts["review_sampled"] == 2
    text = format_report(report)
    assert "Отбор правилами: проверено 5, отсеяно 2 (R-GEO-COUNTRY-ONLY 2), к оценке 3" in text
    assert "В очередь разбора отсева: 2" in text


def test_format_report_rules_lines() -> None:
    report = FetchReport(
        run_id=uuid.UUID(int=1),
        status=RunStatus.OK,
        sources=[],
        outcomes={},
        rules=RulesReport(
            evaluated=9,
            rejected=4,
            by_rule=Counter({"R-STALE": 1, "R-GEO-COUNTRY-ONLY": 3, "R-NOT-REMOTE": 1}),
            sampled=4,
            errors=1,
            first_error="RuntimeError",
        ),
    )
    lines = format_report(report).splitlines()
    assert lines[-2] == (
        "Отбор правилами: проверено 9, отсеяно 4 "
        "(R-GEO-COUNTRY-ONLY 3, R-NOT-REMOTE 1, R-STALE 1), к оценке 5, "
        "ошибок правил 1"
    )
    assert lines[-1] == "В очередь разбора отсева: 4"


def _run_with_rules(session: Session) -> FetchReport:
    with httpx.Client() as client:
        return run_fetch(
            session,
            _adapters(),
            client,
            now=NOW,
            dedup_cfg=DedupConfig(),
            rules_ctx=make_ctx(),
            rng=random.Random(0),
        )


@respx.mock
def test_rule_error_in_a_real_run_makes_it_partial(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_sources()
    calls = {"n": 0}

    def flaky(facts: object, ctx: object) -> str | None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return None

    rules: list[tuple[str, RuleFn]] = [("R-TEST", flaky)]
    monkeypatch.setattr(
        "aijobradar.pipeline.apply_rules",
        lambda session, **kw: apply_rules(session, **{**kw, "rules": rules}),
    )
    report = _run_with_rules(session)
    assert report.status is RunStatus.PARTIAL
    run = session.get(Run, report.run_id)
    assert run is not None and run.status == "partial"
    assert run.counts["rules_errors"] == 1
    assert "ошибок правил 1" in format_report(report)


@respx.mock
def test_apply_rules_crash_keeps_the_ingested_run(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_sources()

    def crash(*args: object, **kwargs: object) -> RulesReport:
        raise OperationalError("x", {}, Exception())

    monkeypatch.setattr("aijobradar.pipeline.apply_rules", crash)
    report = _run_with_rules(session)
    assert report.status is RunStatus.PARTIAL
    run = session.get(Run, report.run_id)
    assert run is not None and run.status == "partial"
    assert run.counts["rules_errors"] == 1
    assert report.rules is not None and report.rules.first_error == "OperationalError"
    assert len(session.scalars(select(Job)).all()) == 5  # ingested jobs survive


@respx.mock
def test_all_sources_failing_stays_failed_with_rules(session: Session) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(503))
    respx.get(API_URL).mock(return_value=httpx.Response(503))
    respx.get(FEED_URL.format(slug="remote-full-stack-programming-jobs")).mock(
        return_value=httpx.Response(503)
    )
    report = _run_with_rules(session)
    assert report.status is RunStatus.FAILED
    assert report.rules is not None and report.rules.errors == 0


@respx.mock
def test_run_without_rules_ctx_has_no_rules_report(session: Session) -> None:
    _mock_sources()
    with httpx.Client() as client:
        report = run_fetch(session, _adapters(), client, now=NOW, dedup_cfg=DedupConfig())
    assert report.rules is None
    run = session.get(Run, report.run_id)
    assert run is not None
    assert not [k for k in run.counts if k.startswith(("rules_", "review_"))]
