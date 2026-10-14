import re
import uuid
from datetime import UTC, date, datetime

import pytest

from aijobradar.db.models import Job, JobSource, LlmScore
from aijobradar.select import Pick
from aijobradar.telegram.render import (
    MAX_MESSAGE,
    callback_data,
    counted_keyboard,
    keyboard,
    parse_callback,
    render_crash,
    render_footer,
    render_header,
    render_job,
)

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def _pick(
    *,
    title: str = "Backend Engineer",
    company: str = "Acme",
    url: str = "https://x.example/1",
    slot: str = "top",
    note: str = "Node.js и интеграции.",
) -> Pick:
    job = Job(
        id=uuid.uuid4(),
        company_raw=company,
        title_raw=title,
        salary_min=60000.0,
        salary_max=80000.0,
        salary_currency="EUR",
        salary_period="year",
    )
    score = LlmScore(final_score=82.5, why_fit=note, main_risk="Нет ставки.", gaps="Нет Kafka.")
    sources = [
        JobSource(source="himalayas", source_url=url),
        JobSource(source="wwr", source_url=url),
    ]
    return Pick(job=job, score=score, slot=slot, sources=sources)


def test_job_message_has_score_salary_links_and_notes() -> None:
    text = render_job(_pick(), 1)
    for fragment in (
        "Backend Engineer",
        "Acme",
        "82/100",
        "60000–80000 EUR/year",
        'href="https://x.example/1"',
        "via Himalayas",
        "via We Work Remotely",
        "Node.js и интеграции.",
        "Нет ставки.",
        "Нет Kafka.",
    ):
        assert fragment in text


def test_everything_third_party_is_escaped() -> None:
    evil = "<script>alert(1)</script> & <b>bold</b> _*["
    text = render_job(_pick(title=evil, company=evil, note=evil, url='https://x.example/"><b>'), 1)
    assert "<script>" not in text and "<b>bold" not in text
    assert "&lt;script&gt;" in text and "&amp;" in text
    assert '"><b>' not in text


def test_non_http_links_are_dropped() -> None:
    text = render_job(_pick(url="javascript:alert(1)"), 1)
    assert "javascript:" not in text and "href" not in text


def test_explore_jobs_are_marked() -> None:
    assert "🎲" in render_job(_pick(slot="explore"), 1)
    assert "🎲" not in render_job(_pick(slot="top"), 1)


def test_huge_fields_still_fit_one_message() -> None:
    text = render_job(
        _pick(title="T" * 10_000, company="C" * 10_000, url="https://x.example/" + "a" * 5000), 1
    )
    assert len(text) <= MAX_MESSAGE


def test_footer_is_escaped_and_capped_on_a_line_boundary() -> None:
    report = "\n".join(f"line {i} <&>" for i in range(2000))
    text = render_footer(report)
    assert len(text) <= MAX_MESSAGE and "&lt;&amp;&gt;" in text
    assert text.endswith("…")


@pytest.mark.parametrize("fit", [True, False])
def test_callback_data_round_trip(fit: bool) -> None:
    data = callback_data(123456789012, fit)
    assert len(data.encode()) <= 64
    assert parse_callback(data) == (123456789012, fit)
    assert keyboard(7)["inline_keyboard"][0][0]["callback_data"] == "l:7:1"


@pytest.mark.parametrize("data", ["", "x", "l:abc:1", "l:1:2", "l:-1:1", "l:1", "k:1:1"])
def test_garbage_callback_data_is_rejected(data: str) -> None:
    assert parse_callback(data) is None


def test_header_and_crash() -> None:
    assert "14.10.2026" in render_header(date(2026, 10, 14), 3, 2)
    assert "подходящих нет" in render_header(date(2026, 10, 14), 0, 2)
    crash = render_crash("БД недоступна", None)
    assert "❌" in crash and "БД недоступна" in crash


# --- Added beyond the brief: the requirements are hard limits, so test them at worst case. ---


def test_escaping_expansion_cannot_push_a_job_over_the_limit() -> None:
    # "<" escapes to 4 chars and "&" to 5, so raw-length caps alone do not bound the output.
    job = Job(
        id=uuid.uuid4(),
        company_raw="&" * 5000,
        title_raw="<" * 5000,
        salary_currency="&" * 50,
        salary_period="<" * 50,
    )
    score = LlmScore(final_score=99.0, why_fit="<" * 5000, main_risk="&" * 5000, gaps="<" * 5000)
    url = "https://x.example/?" + "&" * 900  # passes the raw length cap, expands past it
    sources = [JobSource(source=s, source_url=url) for s in ("himalayas", "jobicy", "wwr")]
    text = render_job(Pick(job, score, "top", sources), 1)
    assert len(text) <= MAX_MESSAGE
    assert "&lt;" in text and "&amp;" in text


def test_long_crash_reason_is_capped_and_keeps_the_marker() -> None:
    text = render_crash("x" * 10_000, None)
    assert len(text) <= MAX_MESSAGE and "❌" in text


def test_footer_without_line_breaks_never_splits_an_entity() -> None:
    text = render_footer("&" * 5000)
    assert len(text) <= MAX_MESSAGE and text.endswith("…")
    body = text[:-1]
    assert not re.search(r"&[#a-z0-9]*$", body)


def test_callback_parse_accepts_only_ascii_digits() -> None:
    assert parse_callback("l:\u0663:1") is None


@pytest.mark.parametrize(("fit", "text"), [(True, "✅ 👍 учтено"), (False, "✅ 👎 учтено")])
def test_counted_keyboard_is_one_inert_button(fit: bool, text: str) -> None:
    rows = counted_keyboard(fit)["inline_keyboard"]
    assert rows == [[{"text": text, "callback_data": "noop"}]]
    assert parse_callback(rows[0][0]["callback_data"]) is None
