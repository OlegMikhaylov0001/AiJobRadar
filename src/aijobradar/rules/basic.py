import re
from collections.abc import Callable, Iterable
from datetime import timedelta

from aijobradar.rules.clauses import after, before, negated
from aijobradar.rules.context import RuleContext
from aijobradar.rules.facts import JobFacts

_REMOTE = re.compile(r"\bremote\b", re.IGNORECASE)
_DESCRIPTION_HEDGE = re.compile(r"\b(?:remote|optional|optionally|if\s+you|near)\b", re.IGNORECASE)
_CLAUSE_REACH = 200  # sane cap; the clause boundary is what actually clips


def _first(patterns: Iterable[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def _hit(
    patterns: Iterable[re.Pattern[str]], text: str, skip: Callable[[str, re.Match[str]], bool]
) -> str | None:
    """First match, line by line, that the field's own wording does not hedge."""
    for line in text.split("\n"):
        for pattern in patterns:
            for match in pattern.finditer(line):
                if not skip(line, match):
                    return match.group(0)
    return None


def _description_hedged(line: str, match: re.Match[str]) -> bool:
    # "Fully remote. Optional hybrid if you live near Berlin." The hedge must be in the same clause.
    clause = (
        before(line, match.start(), _CLAUSE_REACH)
        + match.group(0)
        + after(line, match.end(), _CLAUSE_REACH)
    )
    return negated(line, match) or bool(_DESCRIPTION_HEDGE.search(clause))


def not_remote(f: JobFacts, ctx: RuleContext) -> str | None:
    for patterns, text, field in (
        (ctx.not_remote_title, f.title, "title"),
        (ctx.not_remote_location, f.location_text, "location"),
    ):
        # "Remote / Hybrid", "Backend Engineer - Remote | Hybrid": remote is offered, not ruled out.
        found = None if _REMOTE.search(text) else _hit(patterns, text, negated)
        if found:
            return f"{field}: {found}"
    found = _hit(ctx.not_remote_description, f.description, _description_hedged)
    return f"description: {found}" if found else None


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
