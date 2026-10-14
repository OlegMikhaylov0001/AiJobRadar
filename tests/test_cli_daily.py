"""`run --deliver`: the outcome matrix of the daily run (spec §6.7), end to end.

Sources and Telegram are mocked with respx, the scorer is a fake, and the database is the real
test database. Digest jobs are seeded as fresh `pending_score` rows (recent by the real clock);
the scoring config used here keeps only postings from the last day, so the old jobs in the source
fixtures are scored but never picked and the digest is exactly the seeded jobs.
"""

import itertools
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import Engine, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from typer.testing import CliRunner, Result

from aijobradar import store
from aijobradar.cli import _LABELS_LOCK_ID, _LOCK_ID, app
from aijobradar.db.models import Delivery, Label
from aijobradar.models import RawJob, SourceResult, SourceStatus
from aijobradar.normalize import normalize
from aijobradar.scoring.claude_cli import ScoreResult, ScoreStatus
from aijobradar.sources import build_adapters as real_build_adapters
from aijobradar.sources.jobicy import API_URL
from aijobradar.sources.wwr import FEED_URL
from tests.cli_support import (
    SCORING,
    FakeScorer,
    cli_db_fixture,  # noqa: F401  (registers the `cli_db` fixture)
    mock_fetch,
    ok_result,
    runs_of,
    seed,
    seed_fresh_pending,
    write_run_files,
)
from tests.conftest import TEST_DATABASE_URL, fixture_path
from tests.test_scoring_engine import AXES

# Routes registered to prove a request was NOT made are, by design, never called.
pytestmark = pytest.mark.respx(assert_all_called=False)

TOKEN = "123:TEST"
SEND = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
WWR = FEED_URL.format(slug="remote-full-stack-programming-jobs")
# Names and titles from the seeded jobs and from the source fixtures: private-chat content only.
LEAKS = [
    "Fresh Engineer",
    "Fresh Company",
    "Umbrella",
    "Hooli",
    "Northwind",
    "Acme",
    "Integrations Engineer",
    "QA Automation",
]


@pytest.fixture
def daily_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Telegram settings, an isolated working directory (no developer `.env`) and a step summary."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    return summary


@pytest.fixture
def scoring_path(tmp_path: Path) -> Path:
    """The public scoring config, but with a one-day freshness window."""
    directory = tmp_path / "scoring"
    directory.mkdir()
    text = SCORING.read_text()
    assert "max_age_days: 14" in text
    (directory / "scoring.yaml").write_text(text.replace("max_age_days: 14", "max_age_days: 1"))
    shutil.copytree(SCORING.parent / "prompts", directory / "prompts")
    return directory / "scoring.yaml"


def _telegram_ok(router: respx.MockRouter) -> respx.Route:
    ids = itertools.count(1)

    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": next(ids)}})

    return router.post(SEND).mock(side_effect=reply)


def _telegram_rejects(router: respx.MockRouter) -> respx.Route:
    body = {"ok": False, "error_code": 400, "description": "Bad Request: SECRET JOB TEXT"}
    return router.post(SEND).mock(return_value=httpx.Response(400, json=body))


def _payloads(route: respx.Route) -> list[dict[str, Any]]:
    return [json.loads(call.request.content) for call in route.calls]


def _texts(route: respx.Route) -> list[str]:
    return [p["text"] for p in _payloads(route)]


def _fail_sources(router: respx.MockRouter) -> tuple[respx.Route, respx.Route]:
    return (
        router.get(API_URL).mock(return_value=httpx.Response(503)),
        router.get(WWR).mock(return_value=httpx.Response(503)),
    )


def _deliveries(engine: Engine) -> list[Delivery]:
    with Session(engine) as session:
        return list(session.scalars(select(Delivery).order_by(Delivery.position)))


def _invoke(tmp_path: Path, scoring: Path, *extra: str) -> Result:
    return CliRunner().invoke(app, [*write_run_files(tmp_path, scoring), "--deliver", *extra])


def _assert_no_leaks(*texts: str) -> None:
    for text in texts:
        assert TOKEN not in text
        for leak in LEAKS:
            assert leak not in text, leak


def _zero_score() -> ScoreResult:
    """geo_eligible "no" makes the final score 0: below any threshold."""
    return ScoreResult(
        ScoreStatus.OK, AXES.model_copy(update={"geo_eligible": "no"}), 100, 10, 0.1, "{}", None
    )


def test_daily_ok_sends_digest_and_records_deliveries(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    seed(cli_db)
    seed_fresh_pending(cli_db, 3)
    mock_fetch(respx_mock)
    telegram = _telegram_ok(respx_mock)
    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(ok_result()))

    result = _invoke(tmp_path, scoring_path)

    assert result.exit_code == 0, result.output
    texts = _texts(telegram)
    assert len(texts) == 5  # header + 3 jobs + footer
    assert "Выше порога: 3" in texts[0] and "Сегодня подходящих нет" not in texts[0]
    assert all("Fresh Engineer" in t and "via We Work Remotely" in t for t in texts[1:4])
    assert "Статус прогона" in texts[4] and "Прогон: ok" in texts[4]
    # Job messages carry the feedback buttons; header and footer do not.
    markup = [p.get("reply_markup") for p in _payloads(telegram)]
    assert markup[0] is None and markup[4] is None
    assert all(m and len(m["inline_keyboard"][0]) == 2 for m in markup[1:4])
    deliveries = _deliveries(cli_db)
    assert [(d.slot, d.position) for d in deliveries] == [("top", 1), ("top", 2), ("top", 3)]
    assert all(d.tg_message_id is not None for d in deliveries)
    (run,) = runs_of(cli_db, "fetch")
    assert run.status == "ok"
    assert run.counts["delivered_top"] == 3 and run.counts["delivered_explore"] == 0
    assert "Прогон: ok" in result.output and "Выдача: 3 выше порога" in result.output
    _assert_no_leaks(result.output)


def test_daily_partial_when_a_source_fails(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    seed(cli_db)
    seed_fresh_pending(cli_db, 2)
    respx_mock.get(API_URL).mock(return_value=httpx.Response(503))
    respx_mock.get(WWR).mock(
        return_value=httpx.Response(200, content=fixture_path("wwr", "fullstack.rss").read_bytes())
    )
    telegram = _telegram_ok(respx_mock)
    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(ok_result()))

    result = _invoke(tmp_path, scoring_path)

    assert result.exit_code == 0, result.output
    texts = _texts(telegram)
    assert len(texts) == 4  # the digest is still sent: header + 2 jobs + footer
    assert "Выше порога: 2" in texts[0]
    assert "❌" in texts[-1] and "jobicy" in texts[-1] and "Прогон: partial" in texts[-1]
    assert len(_deliveries(cli_db)) == 2
    (run,) = runs_of(cli_db, "fetch")
    assert run.status == "partial" and run.counts["delivered_top"] == 2
    assert "Прогон: partial" in result.output and "Выдача: 2 выше порога" in result.output
    _assert_no_leaks(result.output)


def test_daily_nothing_above_threshold(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    seed(cli_db)
    seed_fresh_pending(cli_db, 3)
    mock_fetch(respx_mock)
    telegram = _telegram_ok(respx_mock)
    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(_zero_score()))

    result = _invoke(tmp_path, scoring_path)

    assert result.exit_code == 0, result.output
    texts = _texts(telegram)
    # explore_n is 2: two random below-threshold jobs, marked as such, still go out.
    assert len(texts) == 4
    assert "Сегодня подходящих нет" in texts[0] and "🎲 для проверки: 2" in texts[0]
    assert all("🎲" in t and "Оценка: 0/100" in t for t in texts[1:3])
    assert "Прогон: ok" in texts[3]
    assert [d.slot for d in _deliveries(cli_db)] == ["explore", "explore"]
    (run,) = runs_of(cli_db, "fetch")
    assert run.status == "ok"
    assert run.counts["delivered_top"] == 0 and run.counts["delivered_explore"] == 2
    assert "Выдача: подходящих нет + 2 🎲" in result.output
    _assert_no_leaks(result.output)


def test_daily_all_sources_failed_sends_crash_and_exits_1(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    seed(cli_db)
    seed_fresh_pending(cli_db, 2)
    _fail_sources(respx_mock)
    telegram = _telegram_ok(respx_mock)
    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(ok_result()))

    result = _invoke(tmp_path, scoring_path)

    assert result.exit_code == 1, result.output
    (text,) = _texts(telegram)  # exactly one message: the crash notice, no digest
    assert "прогон не удался" in text and "Все источники недоступны" in text
    assert "Прогон: failed" in text  # the report is attached to the crash notice
    assert _deliveries(cli_db) == []
    (run,) = runs_of(cli_db, "fetch")
    assert run.status == "failed"
    assert "Прогон: failed" in result.output
    assert "Прогон: failed" in daily_env.read_text()
    _assert_no_leaks(result.output, daily_env.read_text())


def test_daily_database_down_sends_crash_and_exits_1(
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    # Nothing listens on port 1: the connection is refused at once (connect_timeout is a guard).
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:1/x?connect_timeout=2")
    jobicy = respx_mock.get(API_URL).mock(return_value=httpx.Response(200, json={"jobs": []}))
    wwr = respx_mock.get(WWR).mock(return_value=httpx.Response(200, content=b"<rss/>"))
    telegram = _telegram_ok(respx_mock)

    result = _invoke(tmp_path, scoring_path, "--no-score")

    assert result.exit_code == 1, result.output
    (text,) = _texts(telegram)
    assert "прогон не удался" in text and "БД недоступна" in text
    assert "без неё нельзя исключить повторы" in text
    assert "БД недоступна." in result.stderr
    assert not jobicy.called and not wwr.called  # no source was fetched without a database
    assert "Traceback" not in result.output
    _assert_no_leaks(result.output)


def test_run_migrate_applies_migrations_before_fetching(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    mock_fetch(respx_mock)
    urls: list[str] = []
    monkeypatch.setattr("aijobradar.db.migrate.upgrade", urls.append)

    args = [*write_run_files(tmp_path, scoring_path), "--no-score"]
    result = CliRunner().invoke(app, [*args, "--migrate"])

    assert result.exit_code == 0, result.output
    assert urls == [TEST_DATABASE_URL]
    assert len(runs_of(cli_db, "fetch")) == 1


def test_run_without_migrate_does_not_touch_migrations(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    mock_fetch(respx_mock)
    urls: list[str] = []
    monkeypatch.setattr("aijobradar.db.migrate.upgrade", urls.append)

    result = CliRunner().invoke(app, [*write_run_files(tmp_path, scoring_path), "--no-score"])

    assert result.exit_code == 0, result.output
    assert urls == []


def test_run_migration_failure_sends_crash_and_exits_1_without_leaking(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    def boom(url: str) -> None:
        raise RuntimeError(f"could not connect to {url} / secret job text")

    monkeypatch.setattr("aijobradar.db.migrate.upgrade", boom)
    jobicy = respx_mock.get(API_URL).mock(return_value=httpx.Response(200, json={"jobs": []}))
    wwr = respx_mock.get(WWR).mock(return_value=httpx.Response(200, content=b"<rss/>"))
    telegram = _telegram_ok(respx_mock)

    result = _invoke(tmp_path, scoring_path, "--no-score", "--migrate")

    assert result.exit_code == 1, result.output
    (text,) = _texts(telegram)
    assert "прогон не удался" in text and "Миграция БД не удалась" in text
    assert "Миграция БД не удалась: RuntimeError" in result.stderr
    assert not jobicy.called and not wwr.called
    assert runs_of(cli_db, "fetch") == []
    assert "Traceback" not in result.output
    for leaked in (TEST_DATABASE_URL, "secret job text", TOKEN):
        assert leaked not in result.output and leaked not in text


def test_run_migration_failure_without_deliver_still_exits_1(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    def boom(url: str) -> None:
        raise RuntimeError(url)

    monkeypatch.setattr("aijobradar.db.migrate.upgrade", boom)
    telegram = _telegram_ok(respx_mock)

    args = [*write_run_files(tmp_path, scoring_path), "--no-score", "--migrate"]
    result = CliRunner().invoke(app, args)

    assert result.exit_code == 1
    assert "Миграция БД не удалась: RuntimeError" in result.stderr
    assert not telegram.called and TEST_DATABASE_URL not in result.output


def test_daily_unexpected_crash_send_failure_is_reported_by_class_and_keeps_the_report(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    seed(cli_db)
    _fail_sources(respx_mock)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(f"secret {TOKEN}")

    monkeypatch.setattr("aijobradar.telegram.deliver.send_crash", boom)
    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(ok_result()))

    result = _invoke(tmp_path, scoring_path)

    assert result.exit_code == 1, result.output
    assert "RuntimeError" in result.stderr
    assert "Прогон: failed" in result.output and "Прогон: failed" in daily_env.read_text()
    assert TOKEN not in result.output and "Traceback" not in result.output


def test_daily_crash_reason_when_no_record_reached_the_database(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    seed(cli_db)
    mock_fetch(respx_mock)  # the sources answer; the database rejects every record

    def reject(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("secret job text")

    monkeypatch.setattr("aijobradar.pipeline.ingest", reject)
    telegram = _telegram_ok(respx_mock)
    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(ok_result()))

    result = _invoke(tmp_path, scoring_path)

    assert result.exit_code == 1, result.output
    (text,) = _texts(telegram)
    assert "Ни одна запись не попала в БД" in text
    assert "Все источники недоступны" not in text
    assert "secret job text" not in text and "secret job text" not in result.output


def test_daily_scoring_crash_is_named_in_the_footer_by_class(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    seed(cli_db)
    seed_fresh_pending(cli_db, 1)
    mock_fetch(respx_mock)
    telegram = _telegram_ok(respx_mock)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("secret job text")

    monkeypatch.setattr("aijobradar.cli._score_jobs", boom)

    result = _invoke(tmp_path, scoring_path)

    assert result.exit_code == 0, result.output
    footer = _texts(telegram)[-1]
    assert "⚠️ LLM: оценка прервалась (RuntimeError)" in footer
    assert "Прогон: partial" in footer
    assert "⚠️ LLM: оценка прервалась (RuntimeError)" in result.output
    assert "⚠️ LLM: оценка прервалась (RuntimeError)" in daily_env.read_text()
    assert "secret job text" not in footer and "secret job text" not in result.output


def test_daily_telegram_rejects_exits_1_and_marks_run_failed(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    seed(cli_db)
    seed_fresh_pending(cli_db, 2)
    mock_fetch(respx_mock)
    telegram = _telegram_rejects(respx_mock)
    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(ok_result()))

    result = _invoke(tmp_path, scoring_path)

    assert result.exit_code == 1
    assert "Telegram не принял: sendMessage: HTTP 400" in result.stderr
    assert len(telegram.calls) == 1  # a 400 is not retried and nothing is sent after it
    assert _deliveries(cli_db) == []
    (run,) = runs_of(cli_db, "fetch")
    assert run.status == "failed"
    # Nothing went out: the counts say so instead of being left out.
    assert (
        run.counts["delivered_top"],
        run.counts["delivered_explore"],
        run.counts["delivered_unknown"],
    ) == (0, 0, 0)
    assert "SECRET JOB TEXT" not in result.output  # Telegram's own description is never echoed
    assert "Прогон: failed" in daily_env.read_text()
    assert "Выдача прервана: отправлено 0 выше порога" in result.output
    _assert_no_leaks(result.output, daily_env.read_text())


def test_deliver_without_telegram_settings_exits_2_before_network(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.delenv("TELEGRAM_CHAT_ID")
    seed(cli_db)
    jobicy = respx_mock.get(API_URL).mock(return_value=httpx.Response(200, json={"jobs": []}))
    wwr = respx_mock.get(WWR).mock(return_value=httpx.Response(200, content=b"<rss/>"))
    telegram = _telegram_ok(respx_mock)

    result = _invoke(tmp_path, scoring_path)

    assert result.exit_code == 2
    assert "Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID" in result.output
    assert not jobicy.called and not wwr.called and not telegram.called
    assert runs_of(cli_db, "fetch") == []


def test_second_concurrent_run_is_refused(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    jobicy = respx_mock.get(API_URL).mock(return_value=httpx.Response(200, json={"jobs": []}))
    wwr = respx_mock.get(WWR).mock(return_value=httpx.Response(200, content=b"<rss/>"))
    with cli_db.connect() as holder:
        holder.execute(select(func.pg_advisory_lock(_LOCK_ID)))
        try:
            args = [*write_run_files(tmp_path, scoring_path), "--no-score"]
            result = CliRunner().invoke(app, args)
        finally:
            holder.execute(select(func.pg_advisory_unlock(_LOCK_ID)))
            holder.commit()

    assert result.exit_code == 1
    assert "Другой прогон уже идёт." in result.stderr
    assert not jobicy.called and not wwr.called
    assert runs_of(cli_db, "fetch") == []


def _seed_himalayas_run(engine: Engine, *, status: SourceStatus, hours_ago: float) -> None:
    started = datetime.now(UTC) - timedelta(hours=hours_ago)
    with Session(engine) as session, session.begin():
        run = store.start_run(session, "fetch", started)
        store.record_source_result(
            session, run.id, SourceResult(source="himalayas", status=status, items=[])
        )


@pytest.mark.parametrize(
    ("seeded", "expected"),
    [
        ([(SourceStatus.OK, 29.5)], 36),  # last good run ~30 h ago (the clock moves on): gap + 6 h
        ([(SourceStatus.OK, 24 * 10)], 40),  # a long outage: capped
        ([(SourceStatus.FAILED, 50)], 30),  # a failed run is not a success: the configured window
        # A degraded run is no anchor either: the window stays at the cap, measured from the
        # earlier ok run, so a truncated walk cannot lock the source into a degraded loop.
        ([(SourceStatus.DEGRADED, 50), (SourceStatus.OK, 74)], 40),
        ([], 30),  # no history: the configured window
    ],
)
def test_run_widens_the_himalayas_window_to_the_last_success(
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
    seeded: list[tuple[SourceStatus, float]],
    expected: int,
) -> None:
    mock_fetch(respx_mock)
    for status, hours_ago in seeded:
        _seed_himalayas_run(cli_db, status=status, hours_ago=hours_ago)
    args = write_run_files(tmp_path, scoring_path)
    (tmp_path / "sources.yaml").write_text(
        "himalayas: {lookback_hours: 30, max_lookback_hours: 40}\n"
        "wwr: {feeds: [remote-full-stack-programming-jobs]}\n"
    )
    windows: list[int | None] = []

    def build_adapters(cfg: Any, *, himalayas_lookback_hours: int | None = None) -> Any:
        windows.append(himalayas_lookback_hours)
        # No Himalayas adapter is built, so there is no network for it to reach.
        cfg.himalayas.enabled = False
        return real_build_adapters(cfg, himalayas_lookback_hours=himalayas_lookback_hours)

    monkeypatch.setattr("aijobradar.sources.build_adapters", build_adapters)

    result = CliRunner().invoke(app, [*args, "--no-score"])

    assert result.exit_code == 0, result.output
    assert windows == [expected]


def test_lock_is_released_after_a_run(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    mock_fetch(respx_mock)
    args = [*write_run_files(tmp_path, scoring_path), "--no-score"]
    assert CliRunner().invoke(app, args).exit_code == 0
    # A second run right after the first is not refused: the lock was released.
    again = CliRunner().invoke(app, args)
    assert again.exit_code == 0, again.output
    assert len(runs_of(cli_db, "fetch")) == 2


def test_failing_unlock_does_not_change_the_exit_code_or_lose_the_report(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    mock_fetch(respx_mock)
    monkeypatch.setattr("aijobradar.db.session.make_engine", lambda url: cli_db)

    def refuse_unlock(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if "pg_advisory_unlock" in statement:
            raise RuntimeError("connection is gone")

    event.listen(cli_db, "before_cursor_execute", refuse_unlock)
    try:
        args = [*write_run_files(tmp_path, scoring_path), "--no-score"]
        result = CliRunner().invoke(app, args)
    finally:
        event.remove(cli_db, "before_cursor_execute", refuse_unlock)
        cli_db.dispose()  # closes the pooled connection that still holds the session lock

    assert result.exit_code == 0, result.output
    assert "Прогон: ok" in result.output and "Прогон: ok" in daily_env.read_text()
    assert "Traceback" not in result.output


def test_step_summary_gets_counters_only(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    seed(cli_db)
    seed_fresh_pending(cli_db, 2)
    mock_fetch(respx_mock)
    _telegram_ok(respx_mock)
    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(ok_result()))

    result = _invoke(tmp_path, scoring_path)

    assert result.exit_code == 0, result.output
    summary = daily_env.read_text()
    assert summary.startswith("```\n") and summary.rstrip().endswith("```")
    assert "Прогон:" in summary and "Выдача: 2 выше порога" in summary
    assert "LLM:" in summary
    _assert_no_leaks(summary)


def test_deliver_without_score_sends_jobs_scored_earlier(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    seed(cli_db)
    seed_fresh_pending(cli_db, 2)
    mock_fetch(respx_mock)
    fake = FakeScorer(ok_result())
    monkeypatch.setattr("aijobradar.cli._make_scorer", fake)
    scored = CliRunner().invoke(app, write_run_files(tmp_path, scoring_path))  # no --deliver
    assert scored.exit_code == 0, scored.output
    calls_after_scoring = len(fake.messages)
    telegram = _telegram_ok(respx_mock)

    result = _invoke(tmp_path, scoring_path, "--no-score")

    assert result.exit_code == 0, result.output
    assert len(fake.messages) == calls_after_scoring  # the model was not called again
    texts = _texts(telegram)
    assert len(texts) == 4 and "Выше порога: 2" in texts[0]
    assert len(_deliveries(cli_db)) == 2


GET_UPDATES = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
ANSWER = f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery"


def _seed_delivery(engine: Engine) -> int:
    now = datetime(2026, 10, 14, 9, tzinfo=UTC)
    raw = RawJob(
        source="s",
        source_job_id="labels-1",
        source_url="https://seed.example/labels-1",
        title="Labelled Engineer",
        company="Labelled Company",
        description_html="<p>x</p>",
    )
    with Session(engine) as session, session.begin():
        run = store.start_run(session, "daily", now)
        job = store.insert_job(session, normalize(raw), run.id, now)
        delivery = store.create_delivery(
            session, job_id=job.id, run_id=run.id, slot="top", position=1, now=now
        )
        return delivery.id


def test_labels_poll_records_a_press(
    respx_mock: respx.MockRouter, cli_db: Engine, daily_env: Path
) -> None:
    delivery_id = _seed_delivery(cli_db)
    press = {
        "update_id": 5,
        "callback_query": {
            "id": "cb-5",
            "data": f"l:{delivery_id}:1",
            "message": {"chat": {"id": 42}},
        },
    }
    updates = respx_mock.post(GET_UPDATES).mock(
        return_value=httpx.Response(200, json={"ok": True, "result": [press]})
    )
    answer = respx_mock.post(ANSWER).mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )

    result = CliRunner().invoke(app, ["labels-poll"])

    assert result.exit_code == 0, result.output
    assert "Метки: получено 1, записано 1, пропущено 0." in result.output
    assert json.loads(updates.calls[0].request.content)["offset"] == 0
    assert len(answer.calls) == 1
    with Session(cli_db) as session:
        assert [row.label for row in session.scalars(select(Label))] == ["fit"]
        assert store.get_kv(session, "telegram_update_offset") == "6"
    (run,) = runs_of(cli_db, "labels")
    assert run.status == "ok"
    assert run.counts == {"updates": 1, "labeled": 1, "ignored": 0}


def test_labels_poll_telegram_rejects_exits_1_and_marks_run_failed(
    respx_mock: respx.MockRouter, cli_db: Engine, daily_env: Path
) -> None:
    body = {"ok": False, "error_code": 400, "description": "Bad Request: SECRET"}
    respx_mock.post(GET_UPDATES).mock(return_value=httpx.Response(400, json=body))

    result = CliRunner().invoke(app, ["labels-poll"])

    assert result.exit_code == 1
    assert "Telegram не принял: getUpdates: HTTP 400" in result.stderr
    assert "SECRET" not in result.output and TOKEN not in result.output
    (run,) = runs_of(cli_db, "labels")
    assert run.status == "failed"


def test_labels_poll_without_telegram_settings_exits_2_before_network(
    respx_mock: respx.MockRouter, cli_db: Engine, daily_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.delenv("TELEGRAM_CHAT_ID")
    updates = respx_mock.post(GET_UPDATES)

    result = CliRunner().invoke(app, ["labels-poll"])

    assert result.exit_code == 2
    assert "Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID" in result.output
    assert not updates.called
    assert runs_of(cli_db, "labels") == []


def test_labels_poll_unexpected_error_marks_run_failed_without_leaking(
    cli_db: Engine, daily_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("secret job text")

    monkeypatch.setattr("aijobradar.telegram.labels.poll_labels", boom)

    result = CliRunner().invoke(app, ["labels-poll"])

    assert result.exit_code == 1
    assert "Метки прервались: RuntimeError" in result.stderr
    assert "secret job text" not in result.output and TOKEN not in result.output
    (run,) = runs_of(cli_db, "labels")
    assert run.status == "failed" and run.finished_at is not None


def test_labels_poll_is_refused_while_another_poll_holds_its_lock(
    respx_mock: respx.MockRouter, cli_db: Engine, daily_env: Path
) -> None:
    updates = respx_mock.post(GET_UPDATES)
    with cli_db.connect() as holder:
        holder.execute(select(func.pg_advisory_lock(_LABELS_LOCK_ID)))
        try:
            result = CliRunner().invoke(app, ["labels-poll"])
        finally:
            holder.execute(select(func.pg_advisory_unlock(_LABELS_LOCK_ID)))
            holder.commit()

    assert result.exit_code == 1
    assert "Другой прогон уже идёт." in result.stderr
    assert not updates.called
    assert runs_of(cli_db, "labels") == []


def test_labels_poll_runs_while_the_daily_run_holds_its_lock(
    respx_mock: respx.MockRouter, cli_db: Engine, daily_env: Path
) -> None:
    # The 06:15 poll must not collide with the 06:00 daily run.
    assert _LABELS_LOCK_ID != _LOCK_ID
    respx_mock.post(GET_UPDATES).mock(
        return_value=httpx.Response(200, json={"ok": True, "result": []})
    )
    with cli_db.connect() as holder:
        holder.execute(select(func.pg_advisory_lock(_LOCK_ID)))
        try:
            result = CliRunner().invoke(app, ["labels-poll"])
        finally:
            holder.execute(select(func.pg_advisory_unlock(_LOCK_ID)))
            holder.commit()

    assert result.exit_code == 0, result.output
    (run,) = runs_of(cli_db, "labels")
    assert run.status == "ok"


DOWN_URL = "postgresql://localhost:1/x?connect_timeout=2"  # nothing listens on port 1


def test_labels_poll_database_down_exits_1_without_traceback(
    respx_mock: respx.MockRouter, daily_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", DOWN_URL)
    updates = respx_mock.post(GET_UPDATES)

    result = CliRunner().invoke(app, ["labels-poll"])

    assert result.exit_code == 1
    assert "БД недоступна." in result.stderr
    assert "Traceback" not in result.output and "localhost" not in result.output
    assert not updates.called


def test_db_upgrade_database_down_exits_1_without_traceback(
    daily_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", DOWN_URL)

    result = CliRunner().invoke(app, ["db", "upgrade"])

    assert result.exit_code == 1
    assert "БД недоступна." in result.stderr
    assert "Traceback" not in result.output and "localhost" not in result.output


# --- at-most-once delivery: partial counts and unconfirmed sends ---------------------------------


def _telegram_script(router: respx.MockRouter, script: list[str]) -> respx.Route:
    """One reply per sendMessage call: "ok", "400" or "timeout"; "ok" once the script ends."""
    ids = itertools.count(1)
    steps = iter(script)

    def reply(request: httpx.Request) -> httpx.Response:
        step = next(steps, "ok")
        if step == "timeout":
            raise httpx.ReadTimeout("read timed out", request=request)
        if step == "400":
            return httpx.Response(400, json={"ok": False, "description": "SECRET JOB TEXT"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": next(ids)}})

    return router.post(SEND).mock(side_effect=reply)


def test_daily_refusal_mid_digest_keeps_the_count_of_jobs_already_sent(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    seed(cli_db)
    seed_fresh_pending(cli_db, 2)
    mock_fetch(respx_mock)
    telegram = _telegram_script(respx_mock, ["ok", "ok", "400"])  # header, job 1, job 2 refused
    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(ok_result()))

    result = _invoke(tmp_path, scoring_path)

    assert result.exit_code == 1
    assert len(telegram.calls) == 3
    assert [d.state for d in _deliveries(cli_db)] == ["sent"]  # the refused job's row is gone
    (run,) = runs_of(cli_db, "fetch")
    assert run.status == "failed"
    assert (run.counts["delivered_top"], run.counts["delivered_unknown"]) == (1, 0)
    assert "Выдача прервана: отправлено 1 выше порога" in result.output
    assert "Выдача прервана: отправлено 1 выше порога" in daily_env.read_text()
    _assert_no_leaks(result.output, daily_env.read_text())


def test_daily_read_timeout_on_a_job_is_unconfirmed_and_never_sent_again(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    seed(cli_db)
    seed_fresh_pending(cli_db, 2)
    mock_fetch(respx_mock)
    telegram = _telegram_script(respx_mock, ["ok", "timeout"])  # header, job 1 times out
    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(ok_result()))

    first = _invoke(tmp_path, scoring_path)

    assert first.exit_code == 1
    assert len(telegram.calls) == 2  # the timed-out send is not retried: it may have arrived
    lost_text = _texts(telegram)[1]
    assert "Telegram не принял: sendMessage: ReadTimeout" in first.stderr
    assert [d.state for d in _deliveries(cli_db)] == ["unknown"]
    (run,) = runs_of(cli_db, "fetch")
    assert run.status == "failed" and run.counts["delivered_unknown"] == 1
    assert "Выдача прервана: отправлено 0 выше порога · 1 не подтверждено" in first.output

    second = _invoke(tmp_path, scoring_path, "--no-score")

    assert second.exit_code == 0, second.output
    later = _texts(telegram)[2:]
    assert len(later) == 3  # header + the other job + footer
    assert lost_text not in later
    assert sorted(d.state for d in _deliveries(cli_db)) == ["sent", "unknown"]


# --- a late database failure follows the matrix, not a traceback --------------------------------

SECRET_HOST = "ep-secret.neon.tech"


def _db_gone(*args: Any, **kwargs: Any) -> Any:
    raise OperationalError(
        "SELECT 1", {}, Exception(f'connection to server at "{SECRET_HOST}" failed')
    )


@pytest.mark.parametrize(
    "target",
    ["aijobradar.pipeline.run_fetch", "aijobradar.store.latest_profile_version"],
)
def test_daily_late_database_failure_sends_crash_and_exits_1_without_leaking(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
    target: str,
) -> None:
    seed(cli_db)
    mock_fetch(respx_mock)
    telegram = _telegram_ok(respx_mock)
    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(ok_result()))
    monkeypatch.setattr(target, _db_gone)

    result = _invoke(tmp_path, scoring_path)

    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)  # handled, not an escaped traceback
    assert "Прогон прервался: OperationalError" in result.stderr
    (text,) = _texts(telegram)
    assert "прогон не удался" in text and "Прогон прервался: OperationalError" in text
    summary = daily_env.read_text() if daily_env.exists() else ""
    for shown in (result.output, text, summary):
        assert SECRET_HOST not in shown and "Traceback" not in shown
    _assert_no_leaks(result.output, text)


def test_daily_late_database_failure_after_the_fetch_keeps_the_report(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    seed(cli_db)
    mock_fetch(respx_mock)
    telegram = _telegram_ok(respx_mock)
    monkeypatch.setattr("aijobradar.cli._score_after_fetch", _db_gone)

    result = _invoke(tmp_path, scoring_path)

    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit)
    (text,) = _texts(telegram)
    # The report that already existed goes out, marked failed like the exit code.
    assert "Прогон прервался: OperationalError" in text and "Прогон: failed" in text
    assert "Прогон прервался: OperationalError" in result.stderr
    assert "Прогон: failed" in daily_env.read_text() and "Прогон: failed" in result.output
    assert SECRET_HOST not in result.output + text + daily_env.read_text()


def test_run_without_deliver_late_database_failure_exits_1_without_leaking(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    mock_fetch(respx_mock)
    monkeypatch.setattr("aijobradar.pipeline.run_fetch", _db_gone)

    result = CliRunner().invoke(app, [*write_run_files(tmp_path, scoring_path), "--no-score"])

    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)
    assert "Прогон прервался: OperationalError" in result.stderr
    assert SECRET_HOST not in result.output


def test_score_database_failure_exits_1_without_leaking(
    cli_db: Engine, monkeypatch: pytest.MonkeyPatch, daily_env: Path
) -> None:
    seed(cli_db)
    monkeypatch.setattr("aijobradar.store.start_run", _db_gone)

    result = CliRunner().invoke(app, ["score", "--scoring", str(SCORING)])

    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)
    assert "Оценка прервалась: OperationalError" in result.stderr
    assert SECRET_HOST not in result.output


@pytest.mark.parametrize("target", ["aijobradar.store.start_run", "aijobradar.store.finish_run"])
def test_labels_poll_database_failure_exits_1_without_leaking(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    daily_env: Path,
    target: str,
) -> None:
    respx_mock.post(GET_UPDATES).mock(
        return_value=httpx.Response(200, json={"ok": True, "result": []})
    )
    monkeypatch.setattr(target, _db_gone)

    result = CliRunner().invoke(app, ["labels-poll"])

    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)
    assert "Метки прервались: OperationalError" in result.stderr
    assert SECRET_HOST not in result.output


# --- public report: no third-party text through exception messages -----------------------------


def test_parser_error_text_never_reaches_the_public_report(
    respx_mock: respx.MockRouter,
    cli_db: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    daily_env: Path,
    scoring_path: Path,
) -> None:
    mock_fetch(respx_mock)

    def leaky(self: Any, item: Any) -> Any:
        raise ValueError("could not convert string to float: 'Secret Company text'")

    monkeypatch.setattr("aijobradar.sources.wwr.WwrAdapter.parse_record", leaky)

    result = CliRunner().invoke(app, [*write_run_files(tmp_path, scoring_path), "--no-score"])

    assert result.exit_code == 0, result.output
    summary = daily_env.read_text()
    assert "wwr: ❌ failed" in result.output and "first: ValueError" in result.output
    assert "first: ValueError" in summary
    assert "Secret Company" not in result.output and "Secret Company" not in summary


def test_db_upgrade_other_failure_is_named_by_class_only(
    daily_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(url: str) -> None:
        raise RuntimeError(f"DDL failed on {url} at {SECRET_HOST}")

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setattr("aijobradar.db.migrate.upgrade", boom)

    result = CliRunner().invoke(app, ["db", "upgrade"])

    assert result.exit_code == 1 and isinstance(result.exception, SystemExit)
    assert "Миграция БД не удалась: RuntimeError" in result.stderr
    assert SECRET_HOST not in result.output and TEST_DATABASE_URL not in result.output
