from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import Delivery, LlmScore
from aijobradar.models import RawJob
from aijobradar.normalize import normalize
from aijobradar.select import Pick
from aijobradar.telegram.client import TelegramError
from aijobradar.telegram.deliver import deliver_digest, send_crash

NOW = datetime(2026, 10, 14, 6, tzinfo=UTC)


class FakeTelegram:
    def __init__(self, fail_on: int | None = None) -> None:
        self.sent: list[tuple[str, Any]] = []
        self.fail_on = fail_on

    def send_message(self, chat_id: str, text: str, reply_markup: Any = None) -> int:
        if self.fail_on is not None and len(self.sent) + 1 == self.fail_on:
            raise TelegramError("sendMessage: HTTP 500")
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
    delivered = {d.job_id for d in session.scalars(select(Delivery))}
    assert delivered == {picks[0].job.id}


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
