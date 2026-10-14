"""Send the digest, each job at most once (spec: a job never arrives twice).

Each job's delivery row is committed as `pending` before its send, so a crash at any point
leaves a row that keeps the job out of later digests. After the send the row becomes `sent`;
if Telegram certainly did not take the message it is deleted (the job is offered again next
run); if it may have taken it, the row becomes `unknown` and the job is never sent again
automatically: those are counted in the run report for a manual check."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import Delivery
from aijobradar.select import Pick
from aijobradar.telegram import render
from aijobradar.telegram.client import TelegramClient, TelegramError


@dataclass
class DeliveryReport:
    top: int = 0
    explore: int = 0
    unknown: int = 0  # sends that may or may not have arrived
    interrupted: bool = False  # the digest stopped before its footer


def _settle_failed_send(session: Session, delivery: Delivery, exc: Exception) -> bool:
    """Record a failed send; True if the message may have arrived (row kept as `unknown`)."""
    # A non-Telegram exception cannot tell whether the request left: treat it as ambiguous.
    ambiguous = not isinstance(exc, TelegramError) or exc.ambiguous
    try:
        if ambiguous:
            delivery.state = "unknown"
        else:
            session.delete(delivery)  # certainly not sent: the job stays for the next digest
        session.commit()
    except Exception:
        # The database is gone too: the committed `pending` row stays, which errs on the side
        # of never sending the job twice. The send error is what the caller reports.
        session.rollback()
    return ambiguous


def deliver_digest(
    session: Session,
    client: TelegramClient,
    *,
    chat_id: str,
    run_id: uuid.UUID,
    picks: list[Pick],
    report_text: str,
    now: datetime,
    report: DeliveryReport | None = None,
) -> DeliveryReport:
    """`report` is filled in as jobs go out, so a caller that passes one keeps the counts of
    what was sent even when this raises."""
    report = report if report is not None else DeliveryReport()
    top = sum(p.slot == "top" for p in picks)
    client.send_message(chat_id, render.render_header(now.date(), top, len(picks) - top))
    for position, pick in enumerate(picks, start=1):
        delivery = store.create_delivery(
            session, job_id=pick.job.id, run_id=run_id, slot=pick.slot, position=position, now=now
        )
        session.commit()  # before the send: from here on the job can never be picked again
        text, markup = render.render_job(pick, position), render.keyboard(delivery.id)
        try:
            message_id = client.send_message(chat_id, text, markup)
        except Exception as exc:
            if _settle_failed_send(session, delivery, exc):
                report.unknown += 1
            raise
        if pick.slot == "top":
            report.top += 1
        else:
            report.explore += 1
        delivery.tg_message_id = message_id
        delivery.state = "sent"
        session.commit()
    client.send_message(chat_id, render.render_footer(report_text))
    return report


def send_crash(
    client: TelegramClient, *, chat_id: str, reason: str, report_text: str | None
) -> None:
    client.send_message(chat_id, render.render_crash(reason, report_text))
