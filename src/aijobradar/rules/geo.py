import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aijobradar.rules.clauses import (
    CLAUSE_REACH,
    after,
    before,
    first_hit,
    negated,
    soft,
    temporal,
)
from aijobradar.rules.facts import JobFacts

if TYPE_CHECKING:
    from aijobradar.rules.context import RuleContext

# "{P}" is replaced by Gazetteer.place_group. (?<![\w.]) / (?![\w]) keep "US" from matching
# inside words; the group itself keeps short codes case-sensitive.

# Only title and location are scanned: descriptions are left to the LLM (free text is
# context-blind for regex).
_COUNTRY_ONLY = (
    r"(?<![\w.]){P}(?![\w])\s*[-(]?\s*only\b",
    r"\bonly\s+(?:open\s+to\s+|accepting\s+|hiring\s+)?(?:candidates|applicants|people|residents)?"
    r"\s*(?:who\s+(?:are|live)\s+)?(?:based|located|residing|living)?\s*in\s+(?:the\s+)?{P}(?![\w])",
    r"\bmust\s+(?:be\s+)?(?:based|located|residing|reside|live|living)\s+in\s+(?:the\s+)?{P}(?![\w])",
)
_REMOTE_PLACE = r"\bremote\s*[-–(,:/]\s*{P}(?![\w])"
_RESIDENCY = (
    r"(?<![\w.]){P}(?![\w])\s+(?:residents?|citizens?|nationals?)\s+only\b",
    r"\bonly\s+(?:open\s+to\s+)?(?:residents|citizens|nationals)\s+of\s+(?:the\s+)?{P}(?![\w])",
    r"\b(?:right|authori[sz]ed|eligible|legally\s+(?:able|authori[sz]ed))\s+to\s+work\s+in\s+"
    r"(?:the\s+)?{P}(?![\w])",
    r"\bwork\s+(?:authori[sz]ation|permit)\s+(?:in|for)\s+(?:the\s+)?{P}(?![\w])",
    r"(?<![\w.]){P}(?![\w])\s+citizenship\s+(?:is\s+)?required\b",
)
_CLEARANCE = (
    r"\b(?:active|current|valid|secret|top\s+secret|ts/sci)\s+(?:security\s+)?clearance\b"
    r"|\bsecurity\s+clearance\s+(?:is\s+)?required\b"
    r"|\bmust\s+(?:hold|have|obtain)\s+(?:an?\s+)?(?:active\s+)?security\s+clearance\b"
)


@dataclass(frozen=True)
class GeoPatterns:
    country_only: tuple[re.Pattern[str], ...]
    remote_place: re.Pattern[str]
    residency: tuple[re.Pattern[str], ...]
    clearance: tuple[re.Pattern[str], ...]
    any_place: re.Pattern[str]


def compile_geo_patterns(place_group: str) -> GeoPatterns:
    def c(template: str) -> re.Pattern[str]:
        return re.compile(template.replace("{P}", place_group), re.IGNORECASE)

    def cs(templates: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
        return tuple(c(t) for t in templates)

    return GeoPatterns(
        country_only=cs(_COUNTRY_ONLY),
        remote_place=c(_REMOTE_PLACE),
        residency=cs(_RESIDENCY),
        clearance=cs((_CLEARANCE,)),
        any_place=c(r"(?<![\w.]){P}(?![\w])"),
    )


def _eligible_nearby(line: str, match: re.Match[str], ctx: "RuleContext") -> bool:
    """An eligible place in the same phrase ("US or Europe") makes the restriction non-exclusive."""
    windows = (
        before(line, match.start(), CLAUSE_REACH),
        after(line, match.end(), CLAUSE_REACH),
    )
    return any(
        ctx.gazetteer.canonical(near.group("place")) in ctx.eligible
        for window in windows
        for near in ctx.geo.any_place.finditer(window)
    )


def _hedged(line: str, match: re.Match[str]) -> bool:
    return negated(line, match) or soft(line, match) or temporal(line, match)


def _foreign_place(
    patterns: Iterable[re.Pattern[str]], text: str, ctx: "RuleContext"
) -> str | None:
    """First explicit phrase whose place is known, not eligible, and not hedged."""
    for line in text.split("\n"):
        for pattern in patterns:
            for match in pattern.finditer(line):
                code = ctx.gazetteer.canonical(match.group("place"))
                if code is None or code in ctx.eligible:
                    continue
                if _hedged(line, match) or _eligible_nearby(line, match, ctx):
                    continue
                return f"{code}: {match.group(0)}"
    return None


def _clearance(patterns: Iterable[re.Pattern[str]], text: str) -> str | None:
    found = first_hit(patterns, text, _hedged)
    return f"clearance: {found}" if found else None


def _head(f: JobFacts) -> str:
    # location_text is derived from the structured fields for every current source, so without
    # restrictions it only repeats what the merged (widened) geo already dropped: e.g. a WWR
    # "USA Only" record merged with an unrestricted twin must stay open.
    return f"{f.title}\n{f.location_text}" if f.location_restrictions else f.title


def geo_country_only(f: JobFacts, ctx: "RuleContext") -> str | None:
    if f.location_restrictions:
        codes = [ctx.gazetteer.canonical(t) for t in f.location_restrictions]
        # Any unknown token (a city, "Remote") makes the restriction ambiguous: leave it to the LLM.
        if all(c is not None for c in codes) and not any(c in ctx.eligible for c in codes):
            return "restrictions: " + ", ".join(sorted({c for c in codes if c}))
    head = _head(f)
    return _foreign_place((ctx.geo.remote_place,), head, ctx) or _foreign_place(
        ctx.geo.country_only, head, ctx
    )


def geo_residency(f: JobFacts, ctx: "RuleContext") -> str | None:
    head = _head(f)
    return _foreign_place(ctx.geo.residency, head, ctx) or _clearance(ctx.geo.clearance, head)
