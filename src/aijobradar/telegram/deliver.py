"""Send the digest, each job at most once (spec: a job never arrives twice).

Each job's delivery row is committed as `pending` before its send, so a crash at any point
leaves a row that keeps the job out of later digests. After the send the row becomes `sent`;
if Telegram certainly did not take the message it is deleted (the job is offered again next
run); if it may have taken it, the row becomes `unknown` and the job is never sent again
automatically: those are counted in the run report for a manual check. A row left `pending`
by a run that died around a send becomes `unknown` at the start of the next delivery
(`store.mark_pending_deliveries_unknown`)."""

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


def _settle_failed_send(
    session: Session, delivery: Delivery, exc: Exception, *, requested: bool
) -> bool:
    """Record a failed send; True if the message may have arrived (row kept as `unknown`).

    `requested`: the HTTP call had started. Before it nothing can have reached Telegram; after
    it a non-Telegram exception cannot tell whether the request left, so it is ambiguous."""
    ambiguous = requested and (not isinstance(exc, TelegramError) or exc.ambiguous)
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
        text = render.render_job(pick, position)  # before the row: a failure here leaves none
        delivery = store.create_delivery(
            session, job_id=pick.job.id, run_id=run_id, slot=pick.slot, position=position, now=now
        )
        session.commit()  # before the send: from here on the job can never be picked again
        # Everything between that commit and the answer is settled below; a row left `pending`
        # (the process killed here, or the `sent` commit failing) becomes `unknown` next run.
        requested = False
        try:
            markup = render.keyboard(delivery.id)
            requested = True
            message_id = client.send_message(chat_id, text, markup)
        except Exception as exc:
            if _settle_failed_send(session, delivery, exc, requested=requested):
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
