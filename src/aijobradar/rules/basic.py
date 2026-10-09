import re
from collections.abc import Iterable
from datetime import timedelta

from aijobradar.rules.clauses import first_hit, negated, soft
from aijobradar.rules.context import RuleContext
from aijobradar.rules.facts import JobFacts

_REMOTE = re.compile(r"\bremote\b", re.IGNORECASE)


def _first(patterns: Iterable[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def _hedged(line: str, match: re.Match[str]) -> bool:
    # "Remote (no onsite required)", "Optional hybrid - Berlin": not a requirement.
    return negated(line, match) or soft(line, match)


def not_remote(f: JobFacts, ctx: RuleContext) -> str | None:
    # Title and location only: descriptions are left to the LLM (free text is context-blind).
    for patterns, text, field in (
        (ctx.not_remote_title, f.title, "title"),
        (ctx.not_remote_location, f.location_text, "location"),
    ):
        # "Remote / Hybrid", "Backend Engineer - Remote | Hybrid": remote is offered, not ruled out.
        found = None if _REMOTE.search(text) else first_hit(patterns, text, _hedged)
        if found:
            return f"{field}: {found}"
    return None


def non_engineering_role(f: JobFacts, ctx: RuleContext) -> str | None:
    if _first(ctx.eng_terms, f.title_norm):
        return None
    found = _first(ctx.non_eng_terms, f.title_norm)
    return f"title: {found}" if found else None


def stale(f: JobFacts, ctx: RuleContext) -> str | None:
    if f.posted_at is None:
        return None
    if f.posted_at < ctx.now - timedelta(days=ctx.cfg.stale_days):
        return f"posted {f.posted_at.date().isoformat()}"
    return None
