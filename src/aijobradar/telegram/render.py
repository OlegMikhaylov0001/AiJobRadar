"""Telegram message text (parse_mode HTML). Pure: every third-party string is escaped here."""

import html
import re
from datetime import date
from typing import Any

from aijobradar.db.models import Job
from aijobradar.select import Pick

MAX_MESSAGE = 4096
# Field limits apply to the rendered (escaped) length: escaping can expand text up to 6x.
_TITLE_CHARS = 200
_COMPANY_CHARS = 100
_SALARY_CHARS = 100
_URL_CHARS = 1000
_NOTE_CHARS = 400
_REASON_CHARS = 400
_CALLBACK = re.compile(r"l:([0-9]{1,18}):([01])")
# Attribution required by the sources' terms ("via <Source>", docs/sources.md).
_SOURCE_NAMES = {"himalayas": "Himalayas", "jobicy": "Jobicy", "wwr": "We Work Remotely"}


def _cut_escaped(escaped: str, limit: int) -> str:
    """Cut already-escaped text to `limit` chars without splitting an HTML entity."""
    if len(escaped) <= limit:
        return escaped
    end = limit - 1
    amp = escaped.rfind("&", 0, end)
    if amp != -1 and ";" not in escaped[amp:end]:
        end = amp
    return escaped[:end] + "…"


def _e(text: str, limit: int) -> str:
    """Escape a single-line field and cap its rendered length."""
    return _cut_escaped(html.escape(" ".join(text.split())), limit)


def callback_data(delivery_id: int, fit: bool) -> str:
    return f"l:{delivery_id}:{1 if fit else 0}"


def parse_callback(data: str) -> tuple[int, bool] | None:
    match = _CALLBACK.fullmatch(data)
    return (int(match.group(1)), match.group(2) == "1") if match else None


def keyboard(delivery_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "👍 подходит", "callback_data": callback_data(delivery_id, True)},
                {"text": "👎 мимо", "callback_data": callback_data(delivery_id, False)},
            ]
        ]
    }


def _salary(job: Job) -> str:
    if job.salary_min is None and job.salary_max is None:
        return "ставка не указана"
    low = f"{job.salary_min:.0f}" if job.salary_min is not None else "?"
    high = f"{job.salary_max:.0f}" if job.salary_max is not None else "?"
    return f"{low}–{high} {job.salary_currency or '?'}/{job.salary_period or '?'}"


def _link(source: str, url: str) -> str | None:
    if not url.startswith(("https://", "http://")) or any(c.isspace() for c in url):
        return None
    escaped = html.escape(url, quote=True)
    if len(escaped) > _URL_CHARS:
        return None
    name = _SOURCE_NAMES.get(source, source)
    return f'<a href="{escaped}">via {html.escape(name)}</a>'


def render_header(day: date, top: int, explore: int) -> str:
    stamp = day.strftime("%d.%m.%Y")
    line = "Сегодня подходящих нет" if top == 0 else f"Выше порога: {top}"
    if explore:
        line += f" · 🎲 для проверки: {explore}"
    return f"<b>AiJobRadar · {stamp}</b>\n{line}"


def render_job(pick: Pick, position: int) -> str:
    job, score = pick.job, pick.score
    mark = " 🎲" if pick.slot == "explore" else ""
    links = [link for s in pick.sources if (link := _link(s.source, s.source_url))]
    title = _e(job.title_raw, _TITLE_CHARS)
    company = _e(job.company_raw, _COMPANY_CHARS)
    lines = [
        f"<b>{position}. {title}</b> — {company}{mark}",
        f"Оценка: {score.final_score or 0:.0f}/100 · {_e(_salary(job), _SALARY_CHARS)}",
    ]
    if links:
        lines.append(" · ".join(links))
    for icon, note in (("✅", score.why_fit), ("⚠️", score.main_risk), ("➖", score.gaps)):
        if note:
            lines.append(f"{icon} {_e(note, _NOTE_CHARS)}")
    return _cap_lines("\n".join(lines), MAX_MESSAGE)


def _cap_lines(text: str, limit: int) -> str:
    """Cut text at a line break so no tag or HTML entity is split; hard cut only without one."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit - 2)
    if cut > 0:
        return text[:cut] + "\n…"
    return _cut_escaped(text, limit)


def render_footer(report_text: str) -> str:
    return _cap_lines(f"<b>Статус прогона</b>\n{html.escape(report_text)}", MAX_MESSAGE).rstrip()


def render_crash(reason: str, report_text: str | None) -> str:
    text = f"❌ <b>AiJobRadar: прогон не удался</b>\n{_e(reason, _REASON_CHARS)}"
    if report_text:
        text += f"\n\n{html.escape(report_text)}"
    return _cap_lines(text, MAX_MESSAGE)
