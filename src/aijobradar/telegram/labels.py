"""Turn 👍/👎 presses into labels. Runs every few hours, so answering a press is best effort:
Telegram rejects answers to callback queries older than ~15 seconds.

Updates come from an external API: every field is checked, and anything unexpected is ignored
(and consumed, so it is not read again) instead of failing the poll."""

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import Delivery
from aijobradar.telegram.client import TelegramClient, TelegramError
from aijobradar.telegram.render import parse_callback

OFFSET_KEY = "telegram_update_offset"
_MAX_CALLBACK_ID = 64  # labels.source_ref is String(64)


@dataclass
class LabelsReport:
    updates: int = 0
    labeled: int = 0
    ignored: int = 0


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _label_from(
    session: Session, query: dict[str, Any], chat_id: str, now: datetime
) -> tuple[str, bool] | None:
    """Store the label of one press; (callback id, fit) if it was new, else None."""
    chat = _int(_dict(_dict(query.get("message")).get("chat")).get("id"))
    if chat is None or str(chat) != chat_id:
        return None  # only the owner's chat may label
    callback_id, data = query.get("id"), query.get("data")
    if not isinstance(callback_id, str) or not isinstance(data, str):
        return None
    if not callback_id or len(callback_id) > _MAX_CALLBACK_ID:
        return None  # the id is the dedup key: without one a re-read would label twice
    parsed = parse_callback(data)
    if parsed is None:
        return None
    delivery = session.get(Delivery, parsed[0])
    if delivery is None:
        return None
    fit = parsed[1]
    added = store.add_label(
        session,
        job_id=delivery.job_id,
        label="fit" if fit else "no_fit",
        source="telegram",
        source_ref=callback_id,
        now=now,
    )
    return (callback_id, fit) if added else None


def poll_labels(
    session: Session, client: TelegramClient, *, chat_id: str, now: datetime
) -> LabelsReport:
    report = LabelsReport()
    offset = int(store.get_kv(session, OFFSET_KEY) or 0)
    answered: list[tuple[str, bool]] = []
    for update in client.get_updates(offset):
        report.updates += 1
        fields = _dict(update)
        update_id = _int(fields.get("update_id"))
        if update_id is None:
            report.ignored += 1  # not a real update: nothing to consume or to trust
            continue
        offset = max(offset, update_id + 1)
        query = _dict(fields.get("callback_query"))
        pressed = _label_from(session, query, chat_id, now) if query else None
        if pressed is None:
            report.ignored += 1
        else:
            report.labeled += 1
            answered.append(pressed)
    store.set_kv(session, OFFSET_KEY, str(offset))
    session.commit()  # labels and the new offset land together
    for callback_id, fit in answered:
        with suppress(TelegramError):  # too old to answer: the label is already stored
            client.answer_callback(callback_id, "Записал: 👍" if fit else "Записал: 👎")
    return report
