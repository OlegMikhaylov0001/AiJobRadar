import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import load_scoring_config
from aijobradar.db.models import Delivery, LlmScore
from aijobradar.models import RawJob
from aijobradar.normalize import normalize
from aijobradar.select import Pick, select_digest
from aijobradar.telegram.client import TelegramError
from aijobradar.telegram.deliver import DeliveryReport, deliver_digest, send_crash
from tests.cli_support import cli_db_fixture  # noqa: F401  (registers the `cli_db` fixture)

NOW = datetime(2026, 10, 14, 6, tzinfo=UTC)
CFG = load_scoring_config(Path(__file__).parent.parent / "config" / "scoring.yaml").model_copy(
    update={"threshold": 0.0, "top_n": 10, "explore_n": 0, "max_age_days": 14}
)


class FakeTelegram:
    def __init__(self, fail_on: int | None = None, error: Exception | None = None) -> None:
        self.sent: list[tuple[str, Any]] = []
        self.fail_on = fail_on
        self.error = error or TelegramError("sendMessage: HTTP 400")

    def send_message(self, chat_id: str, text: str, reply_markup: Any = None) -> int:
        if self.fail_on is not None and len(self.sent) + 1 == self.fail_on:
            raise self.error
        self.sent.append((text, reply_markup))
        return 1000 + len(self.sent)


def _picks(session: Session, n: int) -> tuple[Any, list[Pick]]:
    run = store.start_run(session, "daily", NOW)
    profile, _ = store.add_profile_version(
        session, resume_md="R", targets_md="T", note=None, now=NOW
    )
    picks = []
    for i in range(n):
        raw = RawJob(
            source="wwr",
            source_job_id=str(i),
            source_url=f"https://wwr.example/{i}",
            title=f"Engineer {i}",
            company=f"Co {i}",
            description_html="<p>x</p>",
        )
        job = store.insert_job(session, normalize(raw), run.id, NOW - timedelta(hours=1))
        store.attach_source(session, job.id, raw, NOW)
        row = LlmScore(
            job_id=job.id,
            run_id=run.id,
            input_hash=f"h{i}",
            prompt_version="v",
            model="m",
            profile_version_id=profile.id,
            status="ok",
            final_score=90 - i,
            why_fit="Да.",
            scored_at=NOW,
        )
        session.add(row)
        session.flush()
        store.mark_scored(session, [job], row.id)
        picks.append(
            Pick(job, row, "top" if i < 2 else "explore", store.job_sources(session, job.id))
        )
    session.commit()
    return run, picks


def test_digest_is_header_jobs_footer_with_buttons(session: Session) -> None:
    run, picks = _picks(session, 3)
    tg = FakeTelegram()
    report = deliver_digest(
        session,
        tg,
        chat_id="42",
        run_id=run.id,
        picks=picks,  # type: ignore[arg-type]
        report_text="Прогон: ok",
        now=NOW,
    )
    assert (report.top, report.explore) == (2, 1)
    texts = [t for t, _ in tg.sent]
    assert "AiJobRadar" in texts[0] and "Статус прогона" in texts[-1] and len(texts) == 5
    rows = session.scalars(select(Delivery).order_by(Delivery.position)).all()
    assert [r.slot for r in rows] == ["top", "top", "explore"]
    assert [r.tg_message_id for r in rows] == [1002, 1003, 1004]
    assert tg.sent[1][1]["inline_keyboard"][0][0]["callback_data"] == f"l:{rows[0].id}:1"


def test_failure_mid_digest_keeps_sent_jobs_and_drops_the_unsent(session: Session) -> None:
    run, picks = _picks(session, 3)
    tg = FakeTelegram(fail_on=3)  # header ok, job 1 ok, job 2 fails
    with pytest.raises(TelegramError):
        deliver_digest(
            session,
            tg,
            chat_id="42",
            run_id=run.id,
            picks=picks,  # type: ignore[arg-type]
            report_text="x",
            now=NOW,
        )
    delivered = {d.job_id: d.state for d in session.scalars(select(Delivery))}
    assert delivered == {picks[0].job.id: "sent"}


def test_nothing_today_still_sends_header_and_footer(session: Session) -> None:
    run, _ = _picks(session, 0)
    tg = FakeTelegram()
    report = deliver_digest(
        session,
        tg,
        chat_id="42",
        run_id=run.id,
        picks=[],  # type: ignore[arg-type]
        report_text="Прогон: ok",
        now=NOW,
    )
    assert (report.top, report.explore) == (0, 0)
    assert "подходящих нет" in tg.sent[0][0] and len(tg.sent) == 2


def test_crash_message_needs_no_database() -> None:
    tg = FakeTelegram()
    send_crash(tg, chat_id="42", reason="БД недоступна", report_text=None)  # type: ignore[arg-type]
    assert "БД недоступна" in tg.sent[0][0]


def _deliver(session: Session, tg: Any, picks: list[Pick], report: DeliveryReport | None) -> Any:
    return deliver_digest(
        session,
        tg,
        chat_id="42",
        run_id=picks[0].job.first_seen_run_id,
        picks=picks,
        report_text="x",
        now=NOW,
        report=report,
    )


def _selectable(session: Session) -> set[Any]:
    picks = select_digest(session, cfg=CFG, now=NOW, rng=random.Random(1))
    return {p.job.id for p in picks}


def test_a_refused_send_deletes_its_row_and_the_job_is_offered_again(session: Session) -> None:
    _, picks = _picks(session, 3)
    tg = FakeTelegram(fail_on=3, error=TelegramError("sendMessage: HTTP 400"))
    report = DeliveryReport()
    with pytest.raises(TelegramError):
        _deliver(session, tg, picks, report)
    states = {d.job_id: d.state for d in session.scalars(select(Delivery))}
    assert states == {picks[0].job.id: "sent"}
    assert (report.top, report.explore, report.unknown) == (1, 0, 0)  # counts survive the failure
    assert _selectable(session) == {picks[1].job.id, picks[2].job.id}


@pytest.mark.parametrize(
    "error",
    [TelegramError("sendMessage: ReadTimeout", ambiguous=True), RuntimeError("secret")],
    ids=["ambiguous-telegram-error", "unexpected-error"],
)
def test_an_ambiguous_send_is_kept_as_unknown_and_never_offered_again(
    session: Session, error: Exception
) -> None:
    _, picks = _picks(session, 3)
    tg = FakeTelegram(fail_on=3, error=error)  # header ok, job 1 ok, job 2 may have arrived
    report = DeliveryReport()
    with pytest.raises(type(error)):
        _deliver(session, tg, picks, report)
    states = {d.job_id: d.state for d in session.scalars(select(Delivery))}
    assert states == {picks[0].job.id: "sent", picks[1].job.id: "unknown"}
    assert len(tg.sent) == 2  # nothing is sent after it, and it is not sent again
    assert (report.top, report.unknown, report.unresolved) == (1, 1, 1)
    assert _selectable(session) == {picks[2].job.id}


def test_without_a_report_argument_one_is_returned(session: Session) -> None:
    _, picks = _picks(session, 1)
    assert _deliver(session, FakeTelegram(), picks, None).top == 1


class WatchingTelegram(FakeTelegram):
    """Looks at the deliveries table from its own connection while each job is being sent."""

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        self.committed: list[list[tuple[int, str]]] = []

    def send_message(self, chat_id: str, text: str, reply_markup: Any = None) -> int:
        if reply_markup is not None:
            with Session(self.engine) as other:
                rows = other.scalars(select(Delivery).order_by(Delivery.position))
                self.committed.append([(d.position, d.state) for d in rows])
        return super().send_message(chat_id, text, reply_markup)


def test_each_delivery_row_is_committed_as_pending_before_its_send(cli_db: Engine) -> None:
    with Session(cli_db, expire_on_commit=False) as session:
        _, picks = _picks(session, 2)
        tg = WatchingTelegram(cli_db)
        _deliver(session, tg, picks, None)
    # A crash during a send leaves a committed row behind: the job is never sent twice.
    assert tg.committed == [[(1, "pending")], [(1, "sent"), (2, "pending")]]
    with Session(cli_db) as other:
        assert [d.state for d in other.scalars(select(Delivery))] == ["sent", "sent"]


def test_a_render_failure_leaves_no_row(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _, picks = _picks(session, 2)

    def broken(pick: Pick, position: int) -> str:
        raise RuntimeError("secret job text")

    monkeypatch.setattr("aijobradar.telegram.render.render_job", broken)
    tg = FakeTelegram()
    with pytest.raises(RuntimeError):
        _deliver(session, tg, picks, None)
    assert session.scalars(select(Delivery)).all() == []
    assert len(tg.sent) == 1  # the header only


def test_a_failure_between_the_pending_commit_and_the_send_deletes_the_row(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nothing reached Telegram: the job must stay available, not stuck as pending.
    _, picks = _picks(session, 2)

    def broken(delivery_id: int) -> Any:
        raise RuntimeError("keyboard")

    monkeypatch.setattr("aijobradar.telegram.render.keyboard", broken)
    report = DeliveryReport()
    with pytest.raises(RuntimeError):
        _deliver(session, FakeTelegram(), picks, report)
    assert session.scalars(select(Delivery)).all() == []
    assert report.unknown == 0
    assert _selectable(session) == {picks[0].job.id, picks[1].job.id}
