"""Send the digest. A delivery row is written before each send and committed after it, so a
job is never sent twice and a failed send leaves the job for the next run."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.select import Pick
from aijobradar.telegram import render
from aijobradar.telegram.client import TelegramClient


@dataclass
class DeliveryReport:
    top: int = 0
    explore: int = 0


def deliver_digest(
    session: Session,
    client: TelegramClient,
    *,
    chat_id: str,
    run_id: uuid.UUID,
    picks: list[Pick],
    report_text: str,
    now: datetime,
) -> DeliveryReport:
    report = DeliveryReport()
    top = sum(p.slot == "top" for p in picks)
    client.send_message(chat_id, render.render_header(now.date(), top, len(picks) - top))
    for position, pick in enumerate(picks, start=1):
        delivery = store.create_delivery(
            session, job_id=pick.job.id, run_id=run_id, slot=pick.slot, position=position, now=now
        )
        try:
            message_id = client.send_message(
                chat_id, render.render_job(pick, position), render.keyboard(delivery.id)
            )
        except Exception:
            session.rollback()  # not sent: the job stays available for the next digest
            raise
        delivery.tg_message_id = message_id
        session.commit()
        if pick.slot == "top":
            report.top += 1
        else:
            report.explore += 1
    client.send_message(chat_id, render.render_footer(report_text))
    return report


def send_crash(
    client: TelegramClient, *, chat_id: str, reason: str, report_text: str | None
) -> None:
    client.send_message(chat_id, render.render_crash(reason, report_text))
