from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import Label
from aijobradar.models import RawJob
from aijobradar.normalize import normalize
from aijobradar.telegram.client import TelegramError
from aijobradar.telegram.labels import OFFSET_KEY, poll_labels

NOW = datetime(2026, 10, 14, 9, tzinfo=UTC)


class FakeTelegram:
    def __init__(
        self, updates: list[Any], answer_fails: bool = False, edit_fails: bool = False
    ) -> None:
        self.updates = updates
        self.edits: list[tuple[str, int, dict[str, Any] | None]] = []
        self.edit_fails = edit_fails
        self.offsets: list[int] = []
        self.answers: list[str] = []
        self.answer_fails = answer_fails

    def get_updates(self, offset: int) -> list[Any]:
        self.offsets.append(offset)
        return [
            u
            for u in self.updates
            if not isinstance(u, dict)
            or not isinstance(u.get("update_id"), int)
            or u["update_id"] >= offset
        ]

    def answer_callback(self, callback_id: str, text: str) -> None:
        if self.answer_fails:
            raise TelegramError("answerCallbackQuery: HTTP 400")
        self.answers.append(callback_id)

    def edit_reply_markup(
        self, chat_id: str, message_id: int, reply_markup: dict[str, Any] | None
    ) -> None:
        if self.edit_fails:
            raise TelegramError("editMessageReplyMarkup: HTTP 400")
        self.edits.append((chat_id, message_id, reply_markup))


def _delivery(session: Session) -> int:
    run = store.start_run(session, "daily", NOW)
    raw = RawJob(
        source="wwr",
        source_job_id="1",
        source_url="https://wwr.example/1",
        title="Engineer",
        company="Co",
        description_html="<p>x</p>",
    )
    job = store.insert_job(session, normalize(raw), run.id, NOW)
    delivery = store.create_delivery(
        session, job_id=job.id, run_id=run.id, slot="top", position=1, now=NOW
    )
    return delivery.id


def _press(update_id: int, data: str, chat: int = 42, cb: str | None = None) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": cb or f"cb-{update_id}",
            "data": data,
            "message": {"message_id": 500 + update_id, "chat": {"id": chat}},
        },
    }


def _labels(session: Session) -> list[str]:
    return [row.label for row in session.scalars(select(Label).order_by(Label.id))]


def test_presses_become_labels_and_offset_moves(session: Session) -> None:
    d = _delivery(session)
    tg = FakeTelegram([_press(10, f"l:{d}:1"), _press(11, f"l:{d}:0")])
    report = poll_labels(session, tg, chat_id="42", now=NOW)  # type: ignore[arg-type]
    assert (report.updates, report.labeled, report.ignored) == (2, 2, 0)
    assert _labels(session) == ["fit", "no_fit"]
    assert store.get_kv(session, OFFSET_KEY) == "12"
    assert tg.answers == ["cb-10", "cb-11"]
    assert poll_labels(session, tg, chat_id="42", now=NOW).updates == 0  # type: ignore[arg-type]
    assert tg.offsets == [0, 12]


def test_foreign_chats_and_garbage_are_ignored_but_consumed(session: Session) -> None:
    d = _delivery(session)
    tg = FakeTelegram(
        [
            _press(1, f"l:{d}:1", chat=999),
            _press(2, "l:999999:1"),
            _press(3, "garbage"),
            {"update_id": 4, "message": {"text": "hi"}},
        ]
    )
    report = poll_labels(session, tg, chat_id="42", now=NOW)  # type: ignore[arg-type]
    assert (report.updates, report.labeled, report.ignored) == (4, 0, 4)
    assert session.scalars(select(Label)).all() == []
    assert store.get_kv(session, OFFSET_KEY) == "5"
    assert tg.answers == []


def test_malformed_updates_are_ignored_not_fatal(session: Session) -> None:
    d = _delivery(session)
    data = f"l:{d}:1"

    def query(**overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {"id": "cb", "data": data, "message": {"chat": {"id": 42}}}
        return {**base, **overrides}

    tg = FakeTelegram(
        [
            "junk",
            None,
            {"callback_query": query()},  # no update_id
            {"update_id": "5", "callback_query": query()},  # non-int update_id
            {"update_id": True, "callback_query": query()},  # bool is not an update id
            {"update_id": 6, "callback_query": "x"},  # callback_query not a dict
            {"update_id": 7, "callback_query": query(message=None)},  # message missing
            {"update_id": 8, "callback_query": query(message="m")},  # message not a dict
            {"update_id": 9, "callback_query": query(message={"chat": "c"})},  # chat not a dict
            {"update_id": 10, "callback_query": query(message={"chat": {}})},  # chat id missing
            {"update_id": 11, "callback_query": query(data=5)},  # data not a string
            {"update_id": 12, "callback_query": query(id=None)},  # no callback id
            {"update_id": 13, "callback_query": query(id="")},  # empty callback id
            _press(20, data, cb="good"),
        ]
    )
    report = poll_labels(session, tg, chat_id="42", now=NOW)  # type: ignore[arg-type]
    assert (report.updates, report.labeled, report.ignored) == (14, 1, 13)
    assert _labels(session) == ["fit"]
    assert store.get_kv(session, OFFSET_KEY) == "21"
    assert tg.answers == ["good"]


def test_reread_press_is_not_labeled_twice(session: Session) -> None:
    d = _delivery(session)
    first = FakeTelegram([_press(7, f"l:{d}:1", cb="same")])
    poll_labels(session, first, chat_id="42", now=NOW)  # type: ignore[arg-type]
    store.set_kv(session, OFFSET_KEY, "0")  # as if the offset write had been lost
    again = FakeTelegram([_press(7, f"l:{d}:1", cb="same")])
    report = poll_labels(session, again, chat_id="42", now=NOW)  # type: ignore[arg-type]
    assert len(session.scalars(select(Label)).all()) == 1
    assert (report.labeled, report.ignored) == (0, 1)


def test_a_failed_answer_does_not_fail_the_poll(session: Session) -> None:
    d = _delivery(session)
    tg = FakeTelegram([_press(1, f"l:{d}:1")], answer_fails=True)
    report = poll_labels(session, tg, chat_id="42", now=NOW)  # type: ignore[arg-type]
    assert report.labeled == 1
    assert store.get_kv(session, OFFSET_KEY) == "2"


def test_a_corrupted_offset_restarts_from_zero(session: Session) -> None:
    d = _delivery(session)
    store.set_kv(session, OFFSET_KEY, "not-a-number")
    tg = FakeTelegram([_press(3, f"l:{d}:1")])
    report = poll_labels(session, tg, chat_id="42", now=NOW)  # type: ignore[arg-type]
    assert tg.offsets == [0]
    assert report.labeled == 1
    assert store.get_kv(session, OFFSET_KEY) == "4"


def _counted(fit: bool) -> dict[str, Any]:
    text = "✅ 👍 учтено" if fit else "✅ 👎 учтено"
    return {"inline_keyboard": [[{"text": text, "callback_data": "noop"}]]}


def test_a_recorded_press_replaces_the_keyboard_with_a_counted_marker(session: Session) -> None:
    d = _delivery(session)
    tg = FakeTelegram([_press(10, f"l:{d}:1"), _press(11, f"l:{d}:0")])
    poll_labels(session, tg, chat_id="42", now=NOW)  # type: ignore[arg-type]
    assert tg.edits == [("42", 510, _counted(True)), ("42", 511, _counted(False))]


def test_ignored_and_reread_presses_are_not_edited(session: Session) -> None:
    d = _delivery(session)
    first = FakeTelegram([_press(7, f"l:{d}:1", cb="same")])
    poll_labels(session, first, chat_id="42", now=NOW)  # type: ignore[arg-type]
    store.set_kv(session, OFFSET_KEY, "0")
    again = FakeTelegram([_press(7, f"l:{d}:1", cb="same"), _press(8, "garbage")])
    poll_labels(session, again, chat_id="42", now=NOW)  # type: ignore[arg-type]
    assert again.edits == []


def test_a_press_without_a_valid_message_id_is_labeled_but_not_edited(session: Session) -> None:
    d = _delivery(session)

    def with_id(update_id: int, message_id: object) -> dict[str, Any]:
        update = _press(update_id, f"l:{d}:1", cb=f"cb{update_id}")
        update["callback_query"]["message"]["message_id"] = message_id
        return update

    missing = _press(3, f"l:{d}:1", cb="cb3")
    del missing["callback_query"]["message"]["message_id"]
    tg = FakeTelegram([with_id(1, "5"), with_id(2, True), missing])
    report = poll_labels(session, tg, chat_id="42", now=NOW)  # type: ignore[arg-type]
    assert report.labeled == 3
    assert tg.edits == []


def test_a_failed_edit_does_not_fail_the_poll(session: Session) -> None:
    d = _delivery(session)
    tg = FakeTelegram([_press(1, f"l:{d}:1"), _press(2, f"l:{d}:0")], edit_fails=True)
    report = poll_labels(session, tg, chat_id="42", now=NOW)  # type: ignore[arg-type]
    assert report.labeled == 2
    assert store.get_kv(session, OFFSET_KEY) == "3"
    assert tg.answers == ["cb-1", "cb-2"]


def test_a_press_on_the_counted_marker_is_ignored(session: Session) -> None:
    _delivery(session)
    tg = FakeTelegram([_press(1, "noop")])
    report = poll_labels(session, tg, chat_id="42", now=NOW)  # type: ignore[arg-type]
    assert (report.labeled, report.ignored) == (0, 1)
    assert session.scalars(select(Label)).all() == []
    assert tg.edits == [] and tg.answers == []
    assert store.get_kv(session, OFFSET_KEY) == "2"
